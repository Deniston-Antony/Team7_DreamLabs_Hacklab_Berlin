"""Video of every attempt, and attempts that run at once, around one website task.

main.py's `run_task` already decides whether the task succeeded (the simulator's goal check). This adds, for the
same task: an MP4 of the live attempt, and, when the user asks for more than one attempt, headless worker
processes that run the same task from a copy of the scene as it was when the task was submitted and judge it with
the same contact rule (SortFlow's contact_goal_reached). The workers' videos and the live one are joined into a
grid. The logic is SortFlow's (../../SortFlow); this file adapts it to the backend's task dictionary.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import mujoco
import numpy as np

SORTFLOW = Path(__file__).resolve().parents[2] / "SortFlow"
# Appended, so `dream_control` and `yam_env` stay the ones main.py already imported from the installed example.
if str(SORTFLOW) not in sys.path:
    sys.path.append(str(SORTFLOW))
import recording  # noqa: E402

VIDEOS = Path(__file__).resolve().parent / "task_videos"
MAX_ATTEMPTS = 8
STAGGER_S = 0.5  # between worker starts, so they do not open their Dream sessions in one burst
CLIP = re.compile(r"^(attempt-[1-9][0-9]*|grid)$")  # the video names the API serves
POLL_S = 0.5
WAIT_AFTER_LIVE_S = 90  # how long past the time limit a worker may take to report
WORKER_EXIT_S = 10  # how long a stopped worker gets to finish its video


class Workers:
    """Headless attempts of one task, started one after another and collected at the end."""

    def __init__(self, task, env, goal, folder, count, objects=None):
        """Save the scene and start `count` workers in the background.
        @param task The task dictionary: instruction and max_seconds.
        @param env Simulation environment whose current scene the workers copy.
        @param goal {"object": body name, "target": "bowl" or "plate"}, as main.parse_goal returns.
        @param folder Folder for the scene copy and the workers' videos.
        @param count How many workers; their videos are attempt-2.mp4, attempt-3.mp4, ...
        """
        self.folder, self.count, self.procs, self.objects = folder, count, [], objects
        model_path, state_path = folder / "scene.mjb", folder / "scene.npz"
        mujoco.mj_saveModel(env.model, str(model_path))
        np.savez(state_path, qpos=env.data.qpos, qvel=env.data.qvel, ctrl=env.data.ctrl)
        self.command = [
            sys.executable,
            str(SORTFLOW / "parallel_bench.py"),
            "--worker",
            "--task",
            task["instruction"],
            "--seconds",
            str(task["max_seconds"]),
            "--actions",
            "48",
            "--tries",
            "1",
            "--scene-model",
            str(model_path),
            "--scene-state",
            str(state_path),
            "--goal-object",
            goal["object"],
            "--goal-target",
            goal["target"],
        ]
        self.env = dict(os.environ)
        if sys.platform.startswith("linux"):
            self.env.setdefault("MUJOCO_GL", "egl")  # no window for a worker
        if objects:
            backend = Path(__file__).resolve().parent
            paths = [str(backend), str(backend / ".deps")]
            paths += [str(Path(item).resolve()) for item in self.env.get("PYTHONPATH", "").split(os.pathsep) if item]
            self.env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
        self.thread = threading.Thread(target=self._launch, daemon=True)
        self.thread.start()

    def _launch(self):
        for index in range(self.count):
            video = ["--record", str(self.folder / f"attempt-{index + 2}.mp4")]
            if self.objects:
                video += [
                    "--evidence",
                    str(self.folder / f"attempt-{index + 2}.evidence.json"),
                    "--evidence-objects",
                    ",".join(self.objects),
                ]
            self.procs.append(
                subprocess.Popen(
                    self.command + video,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=self.env,
                    cwd=SORTFLOW,
                    start_new_session=True,
                )
            )
            time.sleep(STAGGER_S)

    def stop(self):
        """End every worker now."""
        self.thread.join(timeout=self.count * STAGGER_S + 5)
        self._end_running()

    def _end_running(self):
        running = [proc for proc in self.procs if proc.poll() is None]
        for proc in running:
            proc.terminate()
        deadline = time.monotonic() + WORKER_EXIT_S
        for proc in running:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()

    def results(self, wait_s, cancelled):
        """Wait for the workers and read each one's record.
        @param wait_s Seconds to wait in all before giving up on a worker.
        @param cancelled Callable returning True once the user cancelled the task; the workers are then ended.
        @return One record per worker in start order, or None when the task was cancelled while waiting.
        """
        self.thread.join(timeout=self.count * STAGGER_S + 5)
        deadline = time.monotonic() + wait_s
        while any(proc.poll() is None for proc in self.procs) and time.monotonic() < deadline:
            if cancelled():
                self.stop()
                return None
            time.sleep(POLL_S)
        self._end_running()
        records = []
        for proc in self.procs:
            out, err = proc.communicate()
            lines = [line for line in (out or "").splitlines() if line.startswith("RESULT ")]
            tail = ((err or "").strip().splitlines() or ["no output"])[-1]
            if lines:
                record = json.loads(lines[-1][len("RESULT ") :])
                if str(record.get("error") or "").startswith("exit "):  # dream_control refused to start: say why
                    record["error"] = f"{record['error']}: {tail}"
                records.append(record)
            else:
                records.append({"success": False, "error": f"worker exited {proc.returncode}: {tail}"})
        return records


class Session:
    """One task's recording and hidden attempts: start it before dc.main, call finish() after."""

    def __init__(self, task, env, goal, objects=None):
        """Start recording the live attempt and, when asked and possible, the hidden attempts.
        @param task The task dictionary (task_id, instruction, max_seconds, attempts).
        @param env The backend's simulation environment.
        @param goal {"object", "target"} from main.parse_goal, or None when the instruction cannot be checked.
        """
        self.task, self.env, self.goal = task, env, goal
        self.attempts = int(task.get("attempts", 1))
        self.folder = VIDEOS / task["task_id"]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.workers = None
        if self.attempts > 1 and goal is None:
            task["attempts_note"] = "Hidden attempts need an instruction the checker understands; ran one attempt."
            self.attempts = 1
        elif self.attempts > 1:
            self.workers = Workers(task, env, goal, self.folder, self.attempts - 1, objects)
        self.recorder = recording.attach(env, self.folder / "attempt-1.mp4")
        self.started = env.data.time

    def finish(self, result):
        """Close the video, collect the hidden attempts and add `videos` and `attempt_results` to the result.
        @param result The status dictionary run_task is about to merge into the task; its status and outcome
            describe the live attempt and are left as they are.
        """
        self.recorder.close()
        live_ok = result.get("outcome") == "SUCCESS"
        results = [
            {
                "attempt": 1,
                "where": "live",
                "success": live_ok,
                "sim_seconds": round(self.env.data.time - self.started, 1) if live_ok else None,
            }
        ]
        ended = result.get("status") in ("CANCELLED", "FAILED")
        if self.workers and ended:
            self.workers.stop()
        elif self.workers:
            self.task["waiting_for_attempts"] = True
            records = self.workers.results(
                self.task["max_seconds"] + WAIT_AFTER_LIVE_S, lambda: bool(self.task.get("cancel_requested"))
            )
            self.task["waiting_for_attempts"] = False
            if records is None:
                result["status"], result["result_message"] = "CANCELLED", "ended by the user."
            else:
                for number, record in enumerate(records, 2):
                    results.append(
                        {
                            "attempt": number,
                            "where": "hidden",
                            "success": bool(record.get("success")),
                            "sim_seconds": record.get("sim_seconds"),
                            "closest_cm": record.get("closest_cm"),
                            "error": record.get("error"),
                        }
                    )
        entries = []
        for r in results:
            clip = self.folder / f"attempt-{r['attempt']}.mp4"
            if clip.is_file() and recording.readable(clip):
                seconds = f" {r['sim_seconds']:.0f} s" if r["sim_seconds"] is not None else ""
                label = f"attempt {r['attempt']}: " + (f"OK{seconds}" if r["success"] else "FAIL")
                entries.append((clip, label, r["success"]))
        names = [clip.stem for clip, _, _ in entries]
        if len(entries) > 1 and recording.grid(entries, self.folder / "grid.mp4"):
            names.append("grid")
        result["videos"] = names
        if self.attempts > 1:
            result["attempt_results"] = results
            result["attempts_succeeded"] = sum(1 for r in results if r["success"])
