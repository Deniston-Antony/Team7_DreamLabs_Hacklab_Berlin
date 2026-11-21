"""viewer_workspace.py with a success checker added, and optionally several attempts at once.

Each task is passed through unchanged to Dream Labs' own `dream_control.main`. Around it, a watcher reads the
object positions from MuJoCo, stops the run the moment the named item rests in the named target, and prints the
verdict. With `yam_mujoco_checked.py --tries N` (SORTFLOW_TRIES), the same task runs as N attempts at once: one in
this window, N-1 in headless worker processes (parallel_bench.py --worker), each in its own scene and session, and
the verdict says how many of the N succeeded. Tasks that name no item and target (see sortflow.parse_task) run
exactly as before.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import dream_control
import viewer_workspace
from recording import attach, grid
from sortflow import SuccessWatcher, parse_task

PLACE = {"bowl": "in the bowl", "plate": "on the plate"}
ATTEMPTS = max(1, int(os.environ.get("SORTFLOW_TRIES", "1")))
RECORD = os.environ.get("SORTFLOW_RECORD") == "1"  # set by `yam_mujoco_checked.py --record`
STAGGER_S = 0.5  # between worker starts, so they do not open their Dream sessions in one burst
HERE = Path(__file__).resolve().parent
_original = dream_control.main


def option(argv, name, default=None):
    """The value after `name` in argv, or `default`."""
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


class Workers:
    """Headless attempts of the same task, started one after another and collected at the end."""

    def __init__(self, task, argv, count, videos=None):
        """Start `count` workers in the background.
        @param task The prompt.
        @param argv The viewer task's dream_control arguments, for --seconds, --actions and --model.
        @param count How many workers.
        @param videos Folder for the workers' MP4s (attempt-2.mp4, attempt-3.mp4, ...); None records nothing.
        """
        command = [
            sys.executable,
            str(HERE / "parallel_bench.py"),
            "--worker",
            "--task",
            task,
            "--seconds",
            option(argv, "--seconds", "120"),
            "--actions",
            option(argv, "--actions", "48"),
            "--tries",
            "1",
        ]
        if option(argv, "--model"):
            command += ["--model", option(argv, "--model")]
        self.env = dict(os.environ)
        if sys.platform.startswith("linux"):
            self.env.setdefault("MUJOCO_GL", "egl")  # no window for a worker
        self.command, self.count, self.videos, self.procs = command, count, videos, []
        self.thread = threading.Thread(target=self._launch, daemon=True)
        self.thread.start()

    def _launch(self):
        for index in range(self.count):
            extra = ["--record", str(self.videos / f"attempt-{index + 2}.mp4")] if self.videos else []
            self.procs.append(
                subprocess.Popen(
                    self.command + extra,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=self.env,
                    cwd=HERE,
                    start_new_session=True,
                )
            )
            time.sleep(STAGGER_S)

    def stop(self):
        """End every worker now."""
        self.thread.join(timeout=self.count * STAGGER_S + 5)
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()

    def results(self, wait_s):
        """Wait for the workers and read each one's record.
        @param wait_s Seconds to wait in all before giving up on a worker.
        @return One record per worker, in start order; a worker that fails to report gets an error record.
        """
        self.thread.join(timeout=self.count * STAGGER_S + 5)
        deadline = time.monotonic() + wait_s
        records = []
        for proc in self.procs:
            try:
                out, err = proc.communicate(timeout=max(1.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
            lines = [line for line in (out or "").splitlines() if line.startswith("RESULT ")]
            if lines:
                records.append(json.loads(lines[-1][len("RESULT ") :]))
            else:
                tail = ((err or "").strip().splitlines() or ["no output"])[-1]
                records.append({"success": False, "error": f"worker exited {proc.returncode}: {tail}"})
        return records


def checked_main(argv, shared_env=None, shared_viewer=None, stop_event=None, collision_view=None):
    """Run a task as dream_control.main does, with success detection, and N attempts at once when asked.
    @param argv Command-line arguments for dream_control.main, including --task.
    @param shared_env The viewer process's simulation.
    @param shared_viewer The viewer window.
    @param stop_event Set to end the task; the watcher sets it on success, a user's stop or reset sets it too.
    @param collision_view The collision overlay.
    """
    task = argv[argv.index("--task") + 1] if "--task" in argv else ""
    spec = parse_task(task)
    if not (spec and shared_env is not None and stop_event is not None):
        print("  (No success check: name an item and a bowl or plate, such as: put apple into the bowl)", flush=True)
        return _original(
            argv,
            shared_env=shared_env,
            shared_viewer=shared_viewer,
            stop_event=stop_event,
            collision_view=collision_view,
        )
    item, target = spec
    videos = None
    if RECORD:
        slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:40]
        videos = HERE / "sortflow_results" / "videos" / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
        videos.mkdir(parents=True, exist_ok=True)
    workers = None
    if ATTEMPTS > 1:
        seconds = float(option(argv, "--seconds", "120"))
        print(
            f"  Running {ATTEMPTS} attempts at once: this window and {ATTEMPTS - 1} hidden workers "
            f"(at most about ${ATTEMPTS * seconds / 120 * 0.06:.2f} in credits).",
            flush=True,
        )
        workers = Workers(task, argv, ATTEMPTS - 1, videos)
    recording = attach(shared_env, videos / "attempt-1.mp4") if videos else None
    watcher = SuccessWatcher(shared_env, item, target, stop_event)
    watcher.start()
    try:
        _original(
            argv,
            shared_env=shared_env,
            shared_viewer=shared_viewer,
            stop_event=stop_event,
            collision_view=collision_view,
        )
    except BaseException:
        if workers:
            workers.stop()
        raise
    finally:
        watcher.stop.set()
        watcher.join(timeout=2)
        if recording:
            recording.close()
    window_ok = watcher.success_time is not None
    if workers and not window_ok and (stop_event.is_set() or (shared_viewer and not shared_viewer.is_running())):
        workers.stop()
        print("  Stopped before the task finished; the hidden workers were ended too.", flush=True)
        return
    if workers is None:
        if window_ok:
            print(
                f"  ✓ Success: the {item} is {PLACE[target]} ({watcher.success_time:.1f} s of simulated time).",
                flush=True,
            )
        else:
            print(
                f"  ✗ Not completed: the {item} is not {PLACE[target]} "
                f"(closest it got: {watcher.min_distance * 100:.0f} cm from the {target} centre).",
                flush=True,
            )
        if videos:
            print(f"  Video: {videos / 'attempt-1.mp4'}", flush=True)
        return
    print(f"  This window finished; waiting for {ATTEMPTS - 1} hidden workers...", flush=True)
    records = [
        {"success": window_ok, "sim_seconds": watcher.success_time, "closest_cm": watcher.min_distance * 100}
    ] + workers.results(float(option(argv, "--seconds", "120")) + 90)
    wins = 0
    for number, record in enumerate(records, 1):
        where = "window" if number == 1 else "worker"
        if record.get("success"):
            wins += 1
            print(f"    attempt {number} ({where}): ✓ {record['sim_seconds']:.1f} s", flush=True)
        elif record.get("error"):
            print(f"    attempt {number} ({where}): ✗ {record['error']}", flush=True)
        else:
            print(f"    attempt {number} ({where}): ✗ closest {record.get('closest_cm', 0):.0f} cm", flush=True)
    mark = "✓" if wins else "✗"
    print(f"  {mark} {wins} of {ATTEMPTS} attempts succeeded: {task}", flush=True)
    if videos:
        entries = [
            (
                videos / f"attempt-{number}.mp4",
                f"attempt {number}: " + (f"OK {record['sim_seconds']:.0f} s" if record.get("success") else "FAIL"),
                bool(record.get("success")),
            )
            for number, record in enumerate(records, 1)
        ]
        written = grid(entries, videos / "grid.mp4")
        extra = " (grid.mp4 shows every attempt side by side)" if written else " (no attempt video to join)"
        print(f"  Videos: {videos}{extra}", flush=True)


viewer_workspace.run_task = checked_main  # viewer_workspace calls this name for every task

if __name__ == "__main__":
    viewer_workspace.main()
