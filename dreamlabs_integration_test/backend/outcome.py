"""Video of every attempt, and several attempts or tasks at once, around one website request.

main.py's `run_task` already decides whether the live task succeeded (the simulator's goal check). This adds, for
the same request: an MP4 of the live attempt, and headless worker processes for every other attempt. A request is a
plan of one or more tasks, each with its own number of attempts; the first attempt of the first task is the live
one. Each worker runs its own task from a copy of the scene as it was when the request was submitted, and judges it
with the same contact rule as the live attempt (SortFlow's contact_goal_reached). All the attempts' videos are
joined into one labelled grid. The logic is SortFlow's (../../SortFlow); this file adapts it to the backend.
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
MAX_ATTEMPTS = 8  # attempts in all, over every task of a request: the live one plus at most 7 workers
STAGGER_S = 0.5  # between worker starts, so they do not open their Dream sessions in one burst
CLIP = re.compile(r"^(attempt-[1-9][0-9]*|grid)$")  # the video names the API serves
POLL_S = 0.5
WAIT_AFTER_LIVE_S = 90  # how long past the time limit a worker may take to report


def describe(goal):
    """A short name for a goal, for video labels: "apple>plate"."""
    return f"{goal['object']}>{goal['target']}"


class Workers:
    """Headless attempts, each with its own task, started one after another and collected at the end."""

    def __init__(self, task, env, jobs, folder):
        """Save the scene and start one worker per job in the background.
        @param task The task dictionary: max_seconds.
        @param env Simulation environment whose current scene the workers copy.
        @param jobs List of dicts with `instruction`, `goal` ({"object", "target"} as main.parse_goal returns) and
            `attempt` (the number the attempt has in the request, 2 or more; its video is attempt-<n>.mp4).
        @param folder Folder for the scene copy and the workers' videos.
        """
        self.folder, self.jobs, self.procs = folder, jobs, []
        self.model_path, self.state_path = folder / "scene.mjb", folder / "scene.npz"
        mujoco.mj_saveModel(env.model, str(self.model_path))
        np.savez(self.state_path, qpos=env.data.qpos, qvel=env.data.qvel, ctrl=env.data.ctrl)
        self.seconds = str(task["max_seconds"])
        self.env = dict(os.environ)
        if sys.platform.startswith("linux"):
            self.env.setdefault("MUJOCO_GL", "egl")  # no window for a worker
        self.thread = threading.Thread(target=self._launch, daemon=True)
        self.thread.start()

    def command(self, job):
        """The command line that runs one job."""
        return [
            sys.executable,
            str(SORTFLOW / "parallel_bench.py"),
            "--worker",
            "--task",
            job["instruction"],
            "--seconds",
            self.seconds,
            "--actions",
            "48",
            "--tries",
            "1",
            "--scene-model",
            str(self.model_path),
            "--scene-state",
            str(self.state_path),
            "--goal-object",
            job["goal"]["object"],
            "--goal-target",
            job["goal"]["target"],
            "--record",
            str(self.folder / f"attempt-{job['attempt']}.mp4"),
        ]

    def _launch(self):
        for job in self.jobs:
            self.procs.append(
                subprocess.Popen(
                    self.command(job),
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
        """End every worker now, wait for each to exit and close its pipes, so none is left running or defunct."""
        self.thread.join(timeout=len(self.jobs) * STAGGER_S + 5)
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()

    def results(self, wait_s, cancelled):
        """Wait for the workers and read each one's record.
        @param wait_s Seconds to wait in all before giving up on a worker.
        @param cancelled Callable returning True once the user cancelled the task; the workers are then ended.
        @return One record per job in start order, or None when the task was cancelled while waiting.
        """
        self.thread.join(timeout=len(self.jobs) * STAGGER_S + 5)
        deadline = time.monotonic() + wait_s
        while any(proc.poll() is None for proc in self.procs) and time.monotonic() < deadline:
            if cancelled():
                self.stop()
                return None
            time.sleep(POLL_S)
        records = []
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
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
    """One request's recording and hidden attempts: start it before dc.main, call finish() after."""

    def __init__(self, task, env, goals):
        """Start recording the live attempt and, when asked and possible, every hidden attempt.
        @param task The task dictionary: task_id, max_seconds and `plan`, a list of {"instruction", "attempts"}.
        @param env The backend's simulation environment.
        @param goals One goal per plan entry ({"object", "target"} from main.parse_goal, or None when the
            instruction cannot be checked), in the same order.
        """
        self.task, self.env = task, env
        self.plan = [
            {"instruction": p["instruction"], "attempts": int(p.get("attempts", 1)), "goal": g}
            for p, g in zip(task["plan"], goals)
        ]
        self.folder = VIDEOS / task["task_id"]
        self.folder.mkdir(parents=True, exist_ok=True)
        notes = []
        self.jobs, number = [], 2
        for index, entry in enumerate(self.plan):
            hidden = entry["attempts"] - (1 if index == 0 else 0)
            if hidden and entry["goal"] is None:
                notes.append(f"Task {index + 1} cannot be checked, so its extra attempts were skipped.")
                entry["attempts"] -= hidden
                continue
            for _ in range(hidden):
                self.jobs.append(
                    {"instruction": entry["instruction"], "goal": entry["goal"], "attempt": number, "index": index}
                )
                number += 1
        if notes:
            task["attempts_note"] = " ".join(notes)
        self.workers = Workers(task, env, self.jobs, self.folder) if self.jobs else None
        self.recorder = recording.attach(env, self.folder / "attempt-1.mp4")
        self.started = env.data.time

    def finish(self, result):
        """Close the video, collect the hidden attempts and add `videos` and the attempt results to the result.
        @param result The status dictionary run_task is about to merge into the task; its status and outcome
            describe the live attempt and are left as they are.
        """
        self.recorder.close()
        live_ok = result.get("outcome") == "SUCCESS"
        results = [
            {
                "attempt": 1,
                "task": 1,
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
                for job, record in zip(self.jobs, records):
                    results.append(
                        {
                            "attempt": job["attempt"],
                            "task": job["index"] + 1,
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
            if clip.is_file():
                goal = self.plan[r["task"] - 1]["goal"]
                what = describe(goal) if goal and len(self.plan) > 1 else f"attempt {r['attempt']}"
                label = f"{what} #{r['attempt']}: " + (f"OK {r['sim_seconds']:.0f} s" if r["success"] else "FAIL")
                entries.append((clip, label, r["success"]))
        names = [clip.stem for clip, _, _ in entries]
        if len(entries) > 1 and recording.grid(entries, self.folder / "grid.mp4"):
            names.append("grid")
        result["videos"] = names
        total = sum(p["attempts"] for p in self.plan)
        if total > 1:
            result["attempt_results"] = results
            result["attempts_succeeded"] = sum(1 for r in results if r["success"])
        if len(self.plan) > 1:
            groups = []
            for number, entry in enumerate(self.plan, 1):
                mine = [r for r in results if r["task"] == number]
                groups.append(
                    {
                        "task": number,
                        "instruction": entry["instruction"],
                        "attempts": entry["attempts"],
                        "succeeded": sum(1 for r in mine if r["success"]),
                        "passed": any(r["success"] for r in mine),
                        "results": mine,
                    }
                )
            result["task_results"] = groups
            result["tasks_succeeded"] = sum(1 for g in groups if g["passed"])
