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
import signal
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class StopRequested(BaseException):
    """Raised in a worker when the process that started it asks it to stop (SIGTERM).

    A BaseException, so the `except Exception` that records a failed attempt does not swallow it: the run unwinds
    through its `finally` blocks, which close the video properly and send the final "stopped" status.
    """


def stop_on_sigterm():
    """In a worker, turn the first SIGTERM into StopRequested; later ones are ignored while it cleans up."""

    def handler(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise StopRequested()

    signal.signal(signal.SIGTERM, handler)


import capacity  # noqa: E402


RESULT = "RESULT "  # a worker's last stdout line starts with this, followed by its JSON record
MAX_WORKERS = 16
STAGGER_S = 1.0  # seconds between the first workers' starts, so they do not open their sessions in one burst
HERE = Path(__file__).resolve().parent


def report_time(feed, clock, stop, every=0.5):
    """Keep the website's timer moving while this run waits for a Dream-1 decision."""
    while not stop.wait(every):
        feed.status(feed.state, timer_seconds=round(clock.used(), 1))


def write_evidence(capture, path, record, clock=None):
    """Save a hidden attempt's simulator evidence for the website's evaluator; a failure here never ends the trial."""
    if clock:
        capture.decision_seconds = round(clock.decided(), 2)
        capture.wait_seconds = round(clock.waited(), 2)
    error = str(record.get("error") or "")
    stopped = record.get("stopped") or error == "exit 143"
    status = "COMPLETED" if record["success"] else "CANCELLED" if stopped else "FAILED" if error else "TIMED_OUT"
    proof = capture.finish("pending", status, [error] if error and status == "FAILED" else [], [], [])
    temporary = f"{path}.tmp"
    with open(temporary, "w") as handle:
        handle.write(proof.model_dump_json())
    os.replace(temporary, path)


class ViewFollower:
    """Renders this worker's 3D view with the camera the website is using, only while this run is being watched."""

    def __init__(self, env, path, attempt):
        import mujoco

        self.env, self.path, self.attempt, self.stamp, self.active = env, path, attempt, None, False
        self.camera = mujoco.MjvCamera()
        self.camera.type = mujoco.mjtCamera.mjCAMERA_FREE

    def render(self):
        try:
            stamp = os.stat(self.path).st_mtime_ns
            if stamp != self.stamp:
                with open(self.path) as handle:
                    view = json.load(handle)
                self.stamp = stamp
                self.active = view.get("attempt") == self.attempt and view.get("camera") == "free"
                self.camera.lookat[:] = view["lookat"]
                self.camera.distance, self.camera.azimuth, self.camera.elevation = (
                    view["distance"],
                    view["azimuth"],
                    view["elevation"],
                )
        except (OSError, ValueError, KeyError):
            return None
        if not self.active or self.env.renderer is None:
            return None
        self.env.renderer.update_scene(self.env.data, camera=self.camera)
        return self.env.renderer.render()[60:420].copy()


def capture_evidence(env, objects, dream_control, clock, frames_dir, feed=None):
    """Record evaluator evidence, count movement, decision and wait time, and make Dream-1 calls wait for a free seat.
    @return The EvidenceCapture, or None when the backend's evaluation package cannot be imported.
    """
    capture = None
    try:
        from evaluation.evidence import EvidenceCapture

        capture = EvidenceCapture(
            env, objects, (dream_control.FRAMES - 1) * dream_control.FRAME_EVERY_STEPS + 1, frames_dir
        )
    except Exception as exc:
        print(f"Evidence capture unavailable: {exc}", file=sys.stderr, flush=True)
    rendered = env.images

    def images():
        result = rendered()
        if capture:
            capture.on_step(result)
        clock.tick()
        if feed:
            feed.extra["timer_seconds"] = round(clock.used(), 1)
        return result

    env.images = images
    opened = dream_control.open_policy

    def open_policy(config, key):
        client = capacity.CapacityClient(opened(config, key), clock)
        if capture:
            capture.watch_model_calls(client)
        return client

    dream_control.open_policy = open_policy
    return capture


def run_worker(
    task,
    seconds,
    actions,
    tries,
    runner=None,
    retry_pause=2.0,
    model=None,
    video=None,
    scene=None,
    goal=None,
    live=None,
    evidence=None,
    view=None,
    final_state=None,
    round_trip_log=None,
):
    """Run one trial in this process: up to `tries` attempts, each from a reset scene.
    @param task The prompt, such as "put orange into the bowl"; it must name an item and a bowl or plate.
    @param seconds Movement, Dream-1 decision and seat-wait time per attempt when `evidence` is given, else a wall-clock limit.
    @param actions Rows executed per response, 1 to 48.
    @param tries Attempts allowed; the trial succeeds if any attempt does.
    @param runner Stands in for dream_control.main in tests; None uses the real one.
    @param retry_pause Seconds to wait before re-asking after a retryable refusal without a stated wait.
    @param model Model id for dream_control's --model; None uses its default.
    @param video Path of an MP4 to write the top camera to; None records nothing.
    @param live Binary stream (a pipe) to send the three cameras' newest frames and the trial's status to; None sends
        nothing. A reader that is slow or gone never slows the trial (see livefeed.py).
    @param evidence (json_path, object_names) to save the simulator evidence the website's evaluator reads; None saves
        nothing.
    @param view (json_path, run number) of the website's 3D camera, followed while this run is being watched.
    @param final_state Path of an .npz to save the final scene to, so the website can keep rotating it after the run.
    @param round_trip_log Path of a JSON-lines file to log every Dream-1 request to (roundtrip_log.py); None logs none.
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
    run_seconds = capacity.wall_limit(seconds) if evidence else seconds
    argv = ["--task", task, "--headless", "--quiet", "--seconds", str(run_seconds), "--actions", str(actions)]
    if model:
        argv += ["--model", model]
    if round_trip_log:
        argv += ["--round-trip-log", str(round_trip_log)]
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
    feed = None
    if live is not None:
        from livefeed import attach as stream_to

        follower = ViewFollower(env, *view) if view else None
        feed = stream_to(env, live, free=follower.render if follower else None)  # after the recording, closed before it
    capture = None
    hooked = False
    clock = capacity.AttemptClock(seconds, threading.Event()) if evidence else None
    record = {"task": task, "success": False, "success_try": None, "tries_used": 0, "sim_seconds": None, "error": None}
    closest = float("inf")
    started = time.monotonic()
    watcher = None
    timer_stop = threading.Event()
    timer_thread = None
    if feed and clock:
        timer_thread = threading.Thread(target=report_time, args=(feed, clock, timer_stop), daemon=True)
        timer_thread.start()
    try:
        for attempt in range(1, tries + 1):
            reset()
            if evidence and capture is None and not hooked:
                hooked = True
                capture = capture_evidence(
                    env, evidence[1], dream_control, clock, str(evidence[0]).removesuffix(".evidence.json") + ".frames", feed
                )
            record["tries_used"] = attempt
            for refusals in range(3):  # a retryable refusal (no capacity, say) is asked again, at most twice
                done = threading.Event()
                if clock:
                    clock.stop = done
                watcher = Watcher(env, item, target, done)
                watcher.start()
                error = None
                try:
                    runner(argv, shared_env=env, stop_event=done)
                except SystemExit as stop:
                    error = f"exit {stop.code}"
                except capacity.TimeLimitReached:  # an ordinary timeout, not a failed run
                    error = None
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
    except StopRequested:  # the backend stopped this run: end cleanly, keep the video, report it as stopped
        record.update(error="stopped", stopped=True)
        if watcher is not None:
            closest = min(closest, watcher.min_distance)
    finally:
        timer_stop.set()
        if timer_thread:
            timer_thread.join(timeout=1)
        if clock:
            clock.close()
            record["seat_wait_s"] = round(clock.waited(), 1)  # time spent waiting for a free Dream-1 seat
            record["limit_s"] = float(seconds)
        if capture:
            try:
                write_evidence(capture, evidence[0], record, clock)
            except Exception as exc:
                print(f"Evidence could not be saved: {exc}", file=sys.stderr, flush=True)
        if feed:
            state = (
                "stopped"
                if record.get("stopped")
                else "succeeded"
                if record["success"]
                else "error"
                if record["error"]
                else "failed"
            )
            feed.status(
                state,
                sim_seconds=record["sim_seconds"],
                closest_cm=None if closest == float("inf") else round(closest * 100),
                timer_seconds=round(clock.used(), 1) if clock else None,
            )
            feed.close()
        if final_state:
            try:
                import numpy

                temporary = f"{final_state}.tmp.npz"
                numpy.savez(temporary, qpos=env.data.qpos, qvel=env.data.qvel, ctrl=env.data.ctrl)
                os.replace(temporary, final_state)
            except Exception as exc:
                print(f"Final scene could not be saved: {exc}", file=sys.stderr, flush=True)
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
    evidence = (args.evidence, args.evidence_objects.split(",")) if args.evidence and args.evidence_objects else None
    live = os.fdopen(args.live_fd, "wb", buffering=0) if args.live_fd is not None else None
    view = (args.view_file, args.attempt) if args.view_file and args.attempt else None
    stop_on_sigterm()  # a plain SIGTERM would leave the MP4 unplayable; this lets run_worker finish it
    try:
        record = run_worker(
            args.task,
            args.seconds,
            args.actions,
            args.tries,
            model=args.model,
            video=args.record,
            scene=scene,
            goal=goal,
            live=live,
            evidence=evidence,
            view=view,
            final_state=args.final_state,
            round_trip_log=args.round_trip_log,
        )
    except StopRequested:  # stopped before the run began
        record = {"task": args.task, "success": False, "error": "stopped", "stopped": True}
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
    parser.add_argument("--live-fd", type=int, default=None, help="file descriptor of a pipe to stream frames to")
    parser.add_argument("--attempt", type=int, default=None, help="this worker's run number on the website")
    parser.add_argument("--view-file", default=None, help="JSON file holding the website's 3D camera")
    parser.add_argument("--final-state", default=None, help=".npz path for the final scene")
    parser.add_argument("--evidence", default=None, help="JSON path for a worker's simulator evidence")
    parser.add_argument("--evidence-objects", default=None, help="comma-separated scene objects to record")
    parser.add_argument("--round-trip-log", default=None, help="JSON-lines file to log a worker's Dream-1 requests to")
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
