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
import livefeed  # noqa: E402
import recording  # noqa: E402

VIDEOS = Path(__file__).resolve().parent / "task_videos"
MAX_ATTEMPTS = 8  # attempts in all, over every task of a request: the live one plus at most 7 workers
STAGGER_S = 0.5  # between worker starts, so they do not open their Dream sessions in one burst
CLIP = re.compile(r"^(attempt-[1-9][0-9]*|grid)$")  # the video names the API serves
POLL_S = 0.5
WAIT_AFTER_LIVE_S = 90  # how long past the time limit a worker may take to report
STOP_GRACE_S = 10  # how long a stopped worker may take to close its video and report before it is killed


current = None  # the newest request's Session: its workers' latest frames stay readable after the run ends
previews = None  # prepared parallel environments shown before a run: {"mode", "seed", "count", "envs", "version"}


def describe(goal):
    """A short name for a goal, for video labels: "apple>plate"."""
    return f"{goal['object']}>{goal['target']}"


class Watching:
    """What the backend knows about one hidden attempt while it runs: its newest frames and its status."""

    def __init__(self, job):
        """Start with nothing received.
        @param job The job dict: attempt, instruction, goal, index.
        """
        self.job = job
        self.frames = {}  # camera name -> newest JPEG bytes
        self.status = {}  # the newest status message from the worker
        self.seq = 0  # counts frames received, so a reader can tell when something new has arrived
        self.lock = threading.Lock()

    def read(self, stream):
        """Read the worker's pipe until it closes; runs on its own thread.
        @param stream Binary file object for the read end of the worker's pipe.
        """
        with stream:
            for kind, payload in livefeed.read_messages(stream):
                with self.lock:
                    if kind == livefeed.STATUS:
                        try:
                            self.status = json.loads(payload)
                        except ValueError:
                            continue
                    else:
                        self.frames[livefeed.kind_name(kind)] = payload
                        self.seq += 1


class Workers:
    """Headless attempts, each with its own task, started one after another and collected at the end."""

    def __init__(self, task, env, jobs, folder, objects=None):
        """Save the scene and start one worker per job in the background.
        @param task The task dictionary: max_seconds.
        @param env Simulation environment whose current scene the workers copy.
        @param jobs List of dicts with `instruction`, `goal` ({"object", "target"} as main.parse_goal returns) and
            `attempt` (the number the attempt has in the request, 2 or more; its video is attempt-<n>.mp4).
        @param folder Folder for the scene copy and the workers' videos.
        @param objects Scene objects whose poses each worker saves as evaluation evidence
            (attempt-<n>.evidence.json); None saves no evidence.
        """
        self.folder, self.jobs, self.procs, self.outputs, self.objects = folder, jobs, [], {}, objects
        self.watching = {job["attempt"]: Watching(job) for job in jobs}
        self.model_path, self.state_path = folder / "scene.mjb", folder / "scene.npz"
        mujoco.mj_saveModel(env.model, str(self.model_path))
        np.savez(self.state_path, qpos=env.data.qpos, qvel=env.data.qvel, ctrl=env.data.ctrl)
        for job in jobs:  # a job with its own environment starts from that; the others from the live scene's copy
            environment = job.get("environment")
            job["scene"] = environment.save(folder) if environment is not None else (self.model_path, self.state_path)
        self.seconds = str(task["max_seconds"])
        self.env = dict(os.environ)
        if sys.platform.startswith("linux"):
            self.env.setdefault("MUJOCO_GL", "egl")  # no window for a worker
        if objects:  # the worker imports the backend's evaluation package to capture evidence
            backend = Path(__file__).resolve().parent
            paths = [str(backend), str(backend / ".deps")]
            paths += [str(Path(item).resolve()) for item in self.env.get("PYTHONPATH", "").split(os.pathsep) if item]
            self.env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
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
            str(job["scene"][0]),
            "--scene-state",
            str(job["scene"][1]),
            "--goal-object",
            job["goal"]["object"],
            "--goal-target",
            job["goal"]["target"],
            "--record",
            str(self.folder / f"attempt-{job['attempt']}.mp4"),
        ] + (
            [
                "--evidence",
                str(self.folder / f"attempt-{job['attempt']}.evidence.json"),
                "--evidence-objects",
                ",".join(self.objects),
            ]
            if self.objects
            else []
        )

    def _launch(self):
        for job in self.jobs:
            read_fd, write_fd = os.pipe()  # the worker streams its frames and status into this
            try:
                self.procs.append(
                    subprocess.Popen(
                        self.command(job) + ["--live-fd", str(write_fd)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=self.env,
                        cwd=SORTFLOW,
                        start_new_session=True,
                        pass_fds=(write_fd,),
                    )
                )
            finally:
                os.close(write_fd)  # the worker holds its own copy; ours would keep the pipe open after it exits
            threading.Thread(
                target=self.watching[job["attempt"]].read, args=(os.fdopen(read_fd, "rb"),), daemon=True
            ).start()
            time.sleep(STAGGER_S)

    def _drain(self, index, timeout):
        """Wait for one worker to exit (killing it after `timeout`), and keep its output for collect()."""
        if index in self.outputs:
            return
        proc = self.procs[index]
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        self.outputs[index] = (out or "", err or "")

    def stop(self):
        """Ask every worker to stop and wait for each to exit, so none is left running or defunct.

        Workers stop gracefully on SIGTERM (parallel_bench.StopRequested): each closes its video and reports
        "stopped". One that has not exited after STOP_GRACE_S is killed.
        """
        self.thread.join(timeout=len(self.jobs) * STAGGER_S + 5)
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for index in range(len(self.procs)):
            self._drain(index, STOP_GRACE_S)

    def collect(self):
        """Each started worker's record, from what it printed; call once every worker has exited."""
        records = []
        for index, proc in enumerate(self.procs):
            self._drain(index, STOP_GRACE_S)
            out, err = self.outputs[index]
            lines = [line for line in out.splitlines() if line.startswith("RESULT ")]
            tail = (err.strip().splitlines() or ["no output"])[-1]
            if lines:
                record = json.loads(lines[-1][len("RESULT ") :])
                if str(record.get("error") or "").startswith("exit "):  # dream_control refused to start: say why
                    record["error"] = f"{record['error']}: {tail}"
                records.append(record)
            else:
                records.append({"success": False, "error": f"worker exited {proc.returncode}: {tail}"})
        return records

    def results(self, wait_s, cancelled):
        """Wait for the workers and read each one's record.
        @param wait_s Seconds to wait in all before giving up on a worker.
        @param cancelled Callable returning True once the user cancelled the task; the workers are then stopped.
        @return One record per started job in start order, or None when the task was cancelled while waiting
            (the stopped workers' records are then available from collect()).
        """
        self.thread.join(timeout=len(self.jobs) * STAGGER_S + 5)
        deadline = time.monotonic() + wait_s
        while any(proc.poll() is None for proc in self.procs) and time.monotonic() < deadline:
            if cancelled():
                self.stop()
                return None
            time.sleep(POLL_S)
        for proc in self.procs:
            if proc.poll() is None:  # past the deadline: it will not report any more
                proc.kill()
        return self.collect()


class Session:
    """One request's recording and hidden attempts: start it before dc.main, call finish() after."""

    def __init__(self, task, env, goals, environments=None, objects=None):
        """Start recording the live attempt and, when asked and possible, every hidden attempt.
        @param task The task dictionary: task_id, max_seconds and `plan`, a list of {"instruction", "attempts"}.
        @param env The backend's simulation environment.
        @param goals One goal per plan entry ({"object", "target"} from main.parse_goal, or None when the
            instruction cannot be checked), in the same order.
        @param environments Optional list of environments.Environment, one per hidden run in order: each
            hidden run starts from its own scene (shuffled positions, another style). None starts every hidden
            run from a copy of the live scene.
        @param objects Scene objects the hidden attempts record as evaluation evidence; None records none.
        """
        global current
        self.task, self.env, self.finished, self.workers = task, env, False, None
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
        for position, job in enumerate(self.jobs):
            job["environment"] = environments[position] if environments and position < len(environments) else None
        self.workers = Workers(task, env, self.jobs, self.folder, objects) if self.jobs else None
        self.recorder = recording.attach(env, self.folder / "attempt-1.mp4")
        self.started = env.data.time
        current = self  # last, so the page never sees a half-built session

    def finish(self, result):
        """Close the video, collect the hidden attempts and add `videos` and the attempt results to the result.
        @param result The status dictionary run_task is about to merge into the task; its status and outcome
            describe the live attempt and are left as they are.
        """
        self.recorder.close()
        self.finished = True
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
        results[0]["stopped"] = result.get("status") == "CANCELLED"
        records = []
        if self.workers and ended:
            self.workers.stop()
            records = self.workers.collect()
        elif self.workers:
            self.task["waiting_for_attempts"] = True
            records = self.workers.results(
                self.task["max_seconds"] + WAIT_AFTER_LIVE_S, lambda: bool(self.task.get("cancel_requested"))
            )
            self.task["waiting_for_attempts"] = False
            if records is None:
                result["status"], result["result_message"] = "CANCELLED", "ended by the user."
                records = self.workers.collect()
        for job, record in zip(self.jobs, records):
            results.append(
                {
                    "attempt": job["attempt"],
                    "task": job["index"] + 1,
                    "where": "hidden",
                    "success": bool(record.get("success")),
                    "stopped": bool(record.get("stopped")),
                    "sim_seconds": record.get("sim_seconds"),
                    "closest_cm": record.get("closest_cm"),
                    "error": record.get("error"),
                    "env": job["environment"].label if job.get("environment") is not None else None,
                }
            )
        entries = []
        for r in results:
            clip = self.folder / f"attempt-{r['attempt']}.mp4"
            if clip.is_file() and recording.readable(clip):  # a run killed mid-write leaves an unplayable file
                goal = self.plan[r["task"] - 1]["goal"]
                what = describe(goal) if goal and len(self.plan) > 1 else f"attempt {r['attempt']}"
                seconds = f" {r['sim_seconds']:.0f} s" if r.get("sim_seconds") is not None else ""
                verdict = f"OK{seconds}" if r["success"] else "STOPPED" if r.get("stopped") else "FAIL"
                label = f"{what} #{r['attempt']}: {verdict}"
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

    def states(self):
        """Every attempt's status, for the page's dropdown.
        @return List of dicts: attempt, task, where, label and state. The state is `starting`, `running`,
            `succeeded`, `failed`, `error` or `ended` (a worker that stopped without reporting).
        """
        label = lambda entry: describe(entry["goal"]) if entry["goal"] else entry["instruction"][:30]  # noqa: E731
        out = [
            {
                "attempt": 1,
                "task": 1,
                "where": "live",
                "label": label(self.plan[0]),
                "env": "Live scene",
                "state": "finished" if self.finished else "running",
            }
        ]
        for position, job in enumerate(self.jobs):
            watching = self.workers.watching[job["attempt"]]
            with watching.lock:
                state = watching.status.get("state") or ("running" if watching.frames else "starting")
            proc = self.workers.procs[position] if position < len(self.workers.procs) else None
            if proc is not None and proc.poll() is not None and state in ("starting", "running"):
                state = "ended"
            out.append(
                {
                    "attempt": job["attempt"],
                    "task": job["index"] + 1,
                    "where": "hidden",
                    "label": label(self.plan[job["index"]]),
                    "env": job["environment"].label if job.get("environment") is not None else "Copy of live scene",
                    "state": state,
                }
            )
        return out


def clear_runs():
    """Forget the finished request's parallel runs, so the run tabs no longer show their last pictures.

    Parallel runs only exist while a task runs; afterwards the tabs show what they last saw. A reset changes the
    scene that every next run starts from, so those old pictures would be misleading.
    """
    global current
    current = None


def set_previews(envs, mode, seed):
    """Show prepared environments in the run tabs until the next run.
    @param envs List of environments.Environment with previews rendered.
    @param mode The variation mode they came from.
    @param seed Their seed.
    """
    global previews
    version = (previews or {}).get("version", 0) + 1
    previews = {"mode": mode, "seed": seed, "count": len(envs), "envs": envs, "version": version}


def clear_previews():
    """Stop showing prepared environments."""
    global previews
    previews = None


def attempt_states():
    """Every run's state for the run tabs: the newest request's runs, or the prepared environments before a run.
    @return A list of dicts (attempt, task, where, label, env, state); empty when there is nothing to show.
    """
    if current is not None and current.workers is not None:
        return current.states()
    if previews and previews["envs"]:
        ready = [{"attempt": 1, "task": 1, "where": "live", "label": "", "env": "Live scene", "state": "ready"}]
        return ready + [
            {"attempt": e.attempt, "task": 1, "where": "hidden", "label": "", "env": e.label, "state": "ready"}
            for e in previews["envs"]
        ]
    return []


def worker_frame(attempt, camera):
    """The newest picture of a hidden attempt.
    @param attempt The attempt's number in the request, 2 or more.
    @param camera `top`, `left` or `right`; any other (such as the free 3D view) shows the top camera.
    @return (sequence, JPEG bytes), or (None, None) until the worker has sent its first frame.
    """
    if current is not None and current.workers is not None and attempt in current.workers.watching:
        watching = current.workers.watching[attempt]
        with watching.lock:
            frame = watching.frames.get(camera) or watching.frames.get("top")
            if frame is not None or current.finished:
                return watching.seq, frame
    if previews:  # before a run, or until a run's worker sends its first frame: its environment's starting scene
        for environment in previews["envs"]:
            if environment.attempt == attempt:
                frame = environment.previews.get(camera) or environment.previews.get("top")
                return ("preview", previews["version"], camera), frame
    return None, None
