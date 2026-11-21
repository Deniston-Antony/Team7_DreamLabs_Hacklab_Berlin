"""Run one Dream-1 task many times, several at once, and report how often it succeeds.

Every trial is its own process with its own MuJoCo scene, its own Dream-1 session and no window. A trial stops the
moment the item rests in its target (sortflow.py), and may be tried again from a reset scene (`--tries`).

    python parallel_bench.py --task "put orange into the bowl" --trials 12 --workers 4 --seconds 60
    python parallel_bench.py --task "put apple into the bowl" --trials 8 --workers 4 --tries 3

Each attempt calls the API, so the cost is about trials x tries x seconds / 120 x $0.06 at most.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RESULT = "RESULT "  # a worker's last stdout line starts with this, followed by its JSON record
MAX_WORKERS = 16
STAGGER_S = 1.0  # seconds between the first workers' starts, so they do not open their sessions in one burst
HERE = Path(__file__).resolve().parent


def run_worker(task, seconds, actions, tries, runner=None, retry_pause=2.0, model=None, video=None, scene=None, goal=None):
    """Run one trial in this process: up to `tries` attempts, each from a reset scene.
    @param task The prompt, such as "put orange into the bowl"; it must name an item and a bowl or plate.
    @param seconds Wall-clock limit per attempt.
    @param actions Rows executed per response, 1 to 48.
    @param tries Attempts allowed; the trial succeeds if any attempt does.
    @param runner Stands in for dream_control.main in tests; None uses the real one.
    @param retry_pause Seconds to wait before re-asking after a retryable refusal without a stated wait.
    @param model Model id for dream_control's --model; None uses its default.
    @param video Path of an MP4 to write the top camera to; None records nothing.
    @param scene (model_path, state_path) of a scene to start every attempt from: a compiled MuJoCo model saved with
        mujoco.mj_saveModel and an .npz of its qpos, qvel and ctrl. None uses the default Dream YAM scene.
    @param goal (body_name, target) judged by the website's contact rule, for any body in the scene; None parses the
        task text and uses SortFlow's zone rule for apple, orange, banana and bottle.
    @return The trial's record: success, the attempt it succeeded on, simulated seconds, wall seconds and any error.
    """
    import dream_control
    from sortflow import GoalWatcher, SuccessWatcher, parse_task
    from yam_env import YamEnv

    spec = goal or parse_task(task)
    if spec is None:
        raise ValueError(f"The task must name an item and a bowl or plate: {task!r}")
    item, target = spec
    Watcher = GoalWatcher if goal else SuccessWatcher
    runner = runner or dream_control.main
    argv = ["--task", task, "--headless", "--quiet", "--seconds", str(seconds), "--actions", str(actions)]
    if model:
        argv += ["--model", model]
    env = YamEnv()
    reset = env.reset
    if scene:
        import mujoco
        import numpy as np

        env.model = mujoco.MjModel.from_binary_path(str(scene[0]))
        env.data = mujoco.MjData(env.model)
        start = np.load(scene[1])

        def reset():
            """Put the scene back exactly as it was when the task was submitted."""
            env.data.qpos[:] = start["qpos"]
            env.data.qvel[:] = start["qvel"]
            env.data.ctrl[:] = start["ctrl"]
            mujoco.mj_forward(env.model, env.data)

    recording = None
    if video:
        from recording import attach

        recording = attach(env, video)
    record = {"task": task, "success": False, "success_try": None, "tries_used": 0, "sim_seconds": None, "error": None}
    closest = float("inf")
    started = time.monotonic()
    try:
        for attempt in range(1, tries + 1):
            reset()
            record["tries_used"] = attempt
            for refusals in range(3):  # a retryable refusal (no capacity, say) is asked again, at most twice
                done = threading.Event()
                watcher = Watcher(env, item, target, done)
                watcher.start()
                error = None
                try:
                    runner(argv, shared_env=env, stop_event=done)
                except SystemExit as stop:
                    error = f"exit {stop.code}"
                except Exception as exc:  # recorded, not raised: one bad trial must not end the batch
                    error = f"{type(exc).__name__}: {exc}"
                    wait = getattr(exc, "retry_after_s", None) or retry_pause
                    if getattr(exc, "retryable", False) and refusals < 2:
                        watcher.stop.set()
                        watcher.join(timeout=2)
                        reset()
                        time.sleep(wait)
                        continue
                finally:
                    watcher.stop.set()
                    watcher.join(timeout=2)
                closest = min(closest, watcher.min_distance)
                record["error"] = error
                if watcher.success_time is not None:
                    record.update(success=True, success_try=attempt, sim_seconds=round(watcher.success_time, 1))
                break
            if record["success"] or record["error"]:
                break
    finally:
        if recording:
            recording.close()
        env.close()
    record["wall_seconds"] = round(time.monotonic() - started, 1)
    record["closest_cm"] = None if closest == float("inf") else round(closest * 100)
    return record


def worker_main(args):
    """Entry point of a worker process: run one trial and print its record as the last line."""
    scene = (args.scene_model, args.scene_state) if args.scene_model and args.scene_state else None
    goal = (args.goal_object, args.goal_target) if args.goal_object and args.goal_target else None
    try:
        record = run_worker(args.task, args.seconds, args.actions, args.tries, model=args.model, video=args.record, scene=scene, goal=goal)
    except Exception as exc:
        record = {"task": args.task, "success": False, "error": f"{type(exc).__name__}: {exc}"}
    print(RESULT + json.dumps(record), flush=True)


def worker_command(args):
    """The command that starts one worker process for these arguments."""
    return [
        sys.executable,
        str(HERE / "parallel_bench.py"),
        "--worker",
        "--task",
        args.task,
        "--seconds",
        str(args.seconds),
        "--actions",
        str(args.actions),
        "--tries",
        str(args.tries),
    ]


def run_batch(args, command=None):
    """Run args.trials trials, args.workers at a time, and summarise them.
    @param args Parsed arguments (task, trials, workers, tries, seconds, actions, out).
    @param command The worker command; None uses worker_command(args). Tests pass a stand-in.
    @return The summary dict, also saved as JSON under args.out.
    """
    command = command or worker_command(args)
    env = dict(os.environ)
    if sys.platform.startswith("linux"):
        env.setdefault("MUJOCO_GL", "egl")  # no window, so no display is needed
    lock = threading.Lock()
    results = []
    started = time.monotonic()

    def one(index):
        if index < args.workers:
            time.sleep(index * STAGGER_S)
        proc = subprocess.run(command, capture_output=True, text=True, env=env, cwd=HERE)
        lines = [line for line in proc.stdout.splitlines() if line.startswith(RESULT)]
        if lines:
            record = json.loads(lines[-1][len(RESULT) :])
        else:
            tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
            record = {"task": args.task, "success": False, "error": f"worker exited {proc.returncode}: {tail}"}
        record["trial"] = index + 1
        with lock:
            results.append(record)
            verdict = "PASS" if record["success"] else "FAIL"
            where = f" on try {record['success_try']}" if record.get("success_try") else ""
            note = f" ({record['error']})" if record.get("error") else ""
            print(f"  trial {record['trial']:>2}/{args.trials}: {verdict}{where}{note}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(one, range(args.trials)))
    return summarise(args, sorted(results, key=lambda r: r["trial"]), time.monotonic() - started)


def summarise(args, records, wall):
    """Count outcomes, print the table and save the JSON.
    @param args Parsed arguments.
    @param records One record per trial.
    @param wall Seconds the whole batch took.
    @return The summary dict.
    """
    total = len(records)
    first = sum(1 for r in records if r.get("success_try") == 1)
    wins = [r for r in records if r["success"]]
    times = [r["sim_seconds"] for r in wins if r.get("sim_seconds") is not None]
    summary = {
        "task": args.task,
        "trials": total,
        "workers": args.workers,
        "tries_allowed": args.tries,
        "seconds_per_attempt": args.seconds,
        "first_try_successes": first,
        "successes_within_tries": len(wins),
        "first_try_rate": round(first / total, 2) if total else 0.0,
        "rate_within_tries": round(len(wins) / total, 2) if total else 0.0,
        "median_sim_seconds_to_success": statistics.median(times) if times else None,
        "errors": [r["error"] for r in records if r.get("error")],
        "wall_seconds": round(wall),
        "records": records,
    }
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    path = out / f"parallel-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(summary, indent=2))
    print(f"\n{args.task!r}: {total} trials, {args.workers} at a time, {summary['wall_seconds']} s in all")
    print(f"  first try:        {first}/{total} ({summary['first_try_rate']:.0%})")
    if args.tries > 1:
        print(f"  within {args.tries} tries:   {len(wins)}/{total} ({summary['rate_within_tries']:.0%})")
    print(f"  median time to success: {summary['median_sim_seconds_to_success']} simulated s")
    if summary["errors"]:
        print(f"  errors: {len(summary['errors'])}, first: {summary['errors'][0]}")
    print(f"  saved {path}")
    return summary


def main(argv=None):
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", default="put orange into the bowl")
    parser.add_argument("--trials", type=int, default=8, help="how many independent trials")
    parser.add_argument("--workers", type=int, default=4, help=f"trials running at once, 1 to {MAX_WORKERS}")
    parser.add_argument("--tries", type=int, default=1, help="attempts per trial, each from a reset scene")
    parser.add_argument("--seconds", type=float, default=60, help="wall-clock limit per attempt")
    parser.add_argument("--actions", type=int, default=48, help="rows executed per response, 1 to 48")
    parser.add_argument("--out", default="sortflow_results")
    parser.add_argument("--model", default=None, help="model id for a worker; the default model when omitted")
    parser.add_argument("--record", default=None, help="MP4 path for a worker's top camera")
    parser.add_argument("--scene-model", default=None, help="compiled MuJoCo model (.mjb) a worker starts from")
    parser.add_argument("--scene-state", default=None, help=".npz with that scene's qpos, qvel and ctrl")
    parser.add_argument("--goal-object", default=None, help="body name to judge with the website's contact rule")
    parser.add_argument("--goal-target", default=None, choices=["bowl", "plate"], help="target for --goal-object")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not (
        1 <= args.workers <= MAX_WORKERS and args.trials >= 1 and 1 <= args.tries <= 10 and 1 <= args.actions <= 48
    ):
        parser.error(f"Use workers 1-{MAX_WORKERS}, trials of 1 or more, tries 1-10, actions 1-48.")
    if args.worker:
        return worker_main(args)
    from sortflow import parse_task

    if parse_task(args.task) is None:
        parser.error("The task must name an item and a bowl or plate, such as: put orange into the bowl")
    if not (os.environ.get("DREAM_API_KEY") or (Path.home() / ".dreamlabs" / "credentials").exists()):
        parser.error("Set DREAM_API_KEY or configure ~/.dreamlabs/credentials first.")
    attempts = args.trials * args.tries
    print(
        f"  {args.trials} trials, {args.workers} at a time, up to {attempts} attempts of {args.seconds:g} s: "
        f"at most about ${attempts * args.seconds / 120 * 0.06:.2f} in credits."
    )
    if args.workers > 8:
        print("  Note: more than 8 at once is untested against your account's limits; a busy server answers DA-2000.")
    run_batch(args)


if __name__ == "__main__":
    main()
