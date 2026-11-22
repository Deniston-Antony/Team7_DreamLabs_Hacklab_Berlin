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
import shutil
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
import capacity  # noqa: E402
import livefeed  # noqa: E402
import recording  # noqa: E402

VIDEOS = Path(__file__).resolve().parent / "task_videos"
MAX_ATTEMPTS = 9  # attempts in all, over every task of a request: the live one plus at most 8 workers
STAGGER_S = 1.5  # between worker starts, so they do not open their Dream sessions in one burst
CLIP = re.compile(r"^(attempt-[1-9][0-9]*|grid)$")  # the video names the API serves
POLL_S = 0.5
WORKER_STARTUP_S = 30  # time a hidden attempt may take to load before its own clock starts
STOP_GRACE_S = 10  # how long a stopped worker may take to close its video and report before it is killed


current = None  # the newest request's Session: its workers' latest frames stay readable after the run ends
previews = None  # prepared parallel environments shown before a run: {"mode", "seed", "count", "envs", "version"}


def dream_log_path(folder, attempt):
    """Where run `attempt` of a task logs its Dream-1 requests (SortFlow/roundtrip_log.py), in the task's folder."""
    return Path(folder) / f"attempt-{attempt}.dream.jsonl"


def client_log_path(folder, attempt):
    """Where run `attempt` keeps the dream client's own log (sessions, pacing, warnings): a worker's DREAM_LOG_DIR
    points at this file's folder, and the live run's lines are copied here while it runs."""
    return Path(folder) / f"attempt-{attempt}.client" / "dream.log"


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

    def timer_seconds(self):
        """This run's movement, decision and wait time, as the worker last reported."""
        with self.lock:
            return float(self.status.get("timer_seconds") or 0.0)


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
        self.view_path = folder / "view.json"
        self.model_path, self.state_path = folder / "scene.mjb", folder / "scene.npz"
        mujoco.mj_saveModel(env.model, str(self.model_path))
        np.savez(self.state_path, qpos=env.data.qpos, qvel=env.data.qvel, ctrl=env.data.ctrl)
        for job in jobs:  # a job with its own environment starts from that; the others from the live scene's copy
            if "scene" in job:
                continue
            environment = job.get("environment")
            job["scene"] = environment.save(folder) if environment is not None else (self.model_path, self.state_path)
        self.seconds = str(task["max_seconds"])
        self.deadline = (
            time.monotonic() + capacity.wall_limit(task["max_seconds"]) + WORKER_STARTUP_S + len(jobs) * STAGGER_S
        )
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
            str(job.get("max_seconds") or self.seconds),
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
            "--attempt",
            str(job["attempt"]),
            "--view-file",
            str(self.view_path),
            "--final-state",
            str(self.folder / f"attempt-{job['attempt']}.final.npz"),
            "--round-trip-log",
            str(dream_log_path(self.folder, job["attempt"])),
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
                        env={**self.env, "DREAM_LOG_DIR": str(client_log_path(self.folder, job["attempt"]).parent)},
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

    def progress(self):
        """The longest timer any hidden run has reached."""
        return max((watching.timer_seconds() for watching in self.watching.values()), default=0.0)

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
        overdue = any(proc.poll() is None for proc in self.procs)
        if overdue:
            self.stop()  # lets each late worker finish its video and evidence
        records = self.collect()
        if overdue:
            for record in records:
                if record.get("stopped"):
                    record.update(stopped=False, error=None)
        return records


class Session:
    """One request's recording and hidden attempts: start it before dc.main, call finish() after."""

    has_live = True

    def __init__(self, task, env, goals, environments=None, objects=None, clock=None):
        """Start recording the live attempt and, when asked and possible, every hidden attempt.
        @param task The task dictionary: task_id, max_seconds and `plan`, a list of {"instruction", "attempts"}.
        @param env The backend's simulation environment.
        @param goals One goal per plan entry ({"object", "target"} from main.parse_goal, or None when the
            instruction cannot be checked), in the same order.
        @param environments Optional list of environments.Environment for scenes 2, 3, ...: the n-th run of every
            task starts from scene n, so tasks can be compared on identical layouts. Scene 1 is the live scene.
        @param objects Scene objects the hidden attempts record as evaluation evidence; None records none.
        @param clock The live run's clock.
        """
        global current
        self.task, self.env, self.finished, self.workers = task, env, False, None
        self.clock, self.held, self.live_passed = clock, 0.0, None
        self.plan = [
            {"instruction": p["instruction"], "attempts": int(p.get("attempts", 1)), "goal": g}
            for p, g in zip(task["plan"], goals)
        ]
        self.folder = VIDEOS / task["task_id"]
        self.folder.mkdir(parents=True, exist_ok=True)
        notes = []
        self.jobs, number = [], 2
        for index, entry in enumerate(self.plan):
            first_run = 1 if index == 0 else 0  # task 1's first run is the live run, in scene 1
            hidden = entry["attempts"] - first_run
            if hidden and entry["goal"] is None:
                notes.append(f"Task {index + 1} cannot be checked, so its extra attempts were skipped.")
                entry["attempts"] -= hidden
                continue
            for run in range(first_run, entry["attempts"]):
                self.jobs.append(
                    {
                        "instruction": entry["instruction"],
                        "goal": entry["goal"],
                        "attempt": number,
                        "index": index,
                        "scene_number": run + 1,
                    }
                )
                number += 1
        if notes:
            task["attempts_note"] = " ".join(notes)
        for job in self.jobs:
            position = job["scene_number"] - 2
            job["environment"] = environments[position] if environments and 0 <= position < len(environments) else None
        self.workers = Workers(task, env, self.jobs, self.folder, objects) if self.jobs else None
        if self.workers is None:  # a later suggestion test starts from this saved scene
            mujoco.mj_saveModel(env.model, str(self.folder / "scene.mjb"))
            np.savez(self.folder / "scene.npz", qpos=env.data.qpos, qvel=env.data.qvel, ctrl=env.data.ctrl)
        self.recorder = recording.attach(env, self.folder / "attempt-1.mp4")
        self.started = env.data.time
        current = self  # last, so the page never sees a half-built session

    def progress(self):
        """The run's timer: the longest movement, decision and wait time so far, then the time held to the full limit."""
        live = self.clock.used() if self.clock else 0.0
        hidden = self.workers.progress() if self.workers else 0.0
        return min(float(self.task["max_seconds"]), max(live, hidden, self.held))

    def _hold(self, result, records):
        """Keep the run open until the full time has passed, after every finished run has left Dream-1."""
        limit = float(self.task["max_seconds"])
        reached = self.progress()
        if reached <= 0 or reached >= limit or self.task.get("cancel_requested"):
            return
        if result.get("status") == "FAILED" and all(record.get("error") for record in records):
            return
        self.task["holding"] = True
        started = time.monotonic()
        while not self.task.get("cancel_requested"):
            self.held = min(limit, reached + time.monotonic() - started)
            if self.held >= limit:
                break
            time.sleep(0.1)
        self.task["holding"] = False

    def finish(self, result):
        """Close the video, collect the hidden attempts and add `videos` and the attempt results to the result.
        @param result The status dictionary run_task is about to merge into the task; its status and outcome
            describe the live attempt and are left as they are.
        """
        if self.recorder:
            self.recorder.close()
        live_ok = result.get("outcome") == "SUCCESS"
        self.live_passed = True if live_ok else False if result.get("outcome") == "FAILURE" or result.get("status") == "FAILED" else None
        self.finished = True
        results = [
            {
                "attempt": 1,
                "task": 1,
                "scene": 1,
                "where": "live",
                "success": live_ok,
                "sim_seconds": round(self.env.data.time - self.started, 1) if live_ok else None,
            }
        ] if self.has_live else []
        ended = result.get("status") == "CANCELLED"  # a failed live run leaves the parallel runs to finish
        if results:
            results[0]["stopped"] = ended
        records = []
        if self.workers and ended:
            self.workers.stop()
            records = self.workers.collect()
        elif self.workers:
            self.task["waiting_for_attempts"] = True
            records = self.workers.results(
                max(0.0, self.workers.deadline - time.monotonic()), lambda: bool(self.task.get("cancel_requested"))
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
                    "scene": job["scene_number"],
                    "where": "hidden",
                    "success": bool(record.get("success")),
                    "stopped": bool(record.get("stopped")),
                    "sim_seconds": record.get("sim_seconds"),
                    "closest_cm": record.get("closest_cm"),
                    "error": record.get("error"),
                    "env": job.get("env_label") or (job["environment"].label if job.get("environment") is not None else None),
                    "name": job.get("name"),
                }
            )
        self._hold(result, records)
        self.task["finalizing"] = True
        entries = []
        for r in results:
            clip = self.folder / f"attempt-{r['attempt']}.mp4"
            if clip.is_file() and recording.readable(clip):  # a run killed mid-write leaves an unplayable file
                goal = self.plan[r["task"] - 1]["goal"]
                what = r.get("name") or (f"{describe(goal)} scene {r['scene']}" if goal and len(self.plan) > 1 else f"attempt {r['attempt']}")
                seconds = f" {r['sim_seconds']:.0f} s" if r.get("sim_seconds") is not None else ""
                verdict = f"OK{seconds}" if r["success"] else "STOPPED" if r.get("stopped") else "FAIL"
                label = f"{what} #{r['attempt']}: {verdict}"
                entries.append((clip, label, r["success"]))
        names = [clip.stem for clip, _, _ in entries]
        if len(entries) > 1 and recording.grid(entries, self.folder / "grid.mp4"):
            names.append("grid")
        result["videos"] = names
        total = sum(p["attempts"] for p in self.plan)
        if total > 1 or not self.has_live:  # one selected suggestion test still needs its result saved
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
        @return List of dicts: attempt, task, where, label, state, timer_s and limit_s. The state is `starting`,
            `running`, `succeeded`, `failed`, `error` or `ended` (a worker that stopped without reporting).
        """
        limit = float(self.task["max_seconds"])
        label = lambda entry: describe(entry["goal"]) if entry["goal"] else entry["instruction"][:30]  # noqa: E731
        out = [] if not self.has_live else [
            {
                "attempt": 1,
                "task": 1,
                "scene": 1,
                "where": "live",
                "label": label(self.plan[0]),
                "env": "Live scene",
                "state": "finished" if self.finished else "running",
                "passed": self.live_passed,
                "timer_s": round(self.clock.used(), 1) if self.clock else 0.0,
                "limit_s": limit,
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
                    "scene": job["scene_number"],
                    "where": "hidden",
                    "label": label(self.plan[job["index"]]),
                    "env": job.get("env_label") or (job["environment"].label if job.get("environment") is not None else "Copy of live scene"),
                    "state": state,
                    "timer_s": round(watching.timer_seconds(), 1),
                    "limit_s": float(job.get("max_seconds") or limit),
                    "name": job.get("name"),
                }
            )
        return out


class SuggestionSession(Session):
    """A run of one-change tests. Every test is a hidden worker, so the live scene is never touched."""

    has_live = False

    def __init__(self, task, env, tests):
        """Save each test's starting scene and start its worker.
        @param task The task dictionary.
        @param env The backend's simulation environment.
        @param tests Tests from suggestions.build_tests.
        """
        global current
        self.task, self.env, self.finished, self.clock, self.held, self.live_passed = task, env, False, None, 0.0, None
        self.recorder, self.started = None, env.data.time
        self.folder = VIDEOS / task["task_id"]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.plan, self.jobs = [], []
        for index, test in enumerate(tests):
            goal = {"object": test["target"], "target": test["destination"]}
            self.plan.append({"instruction": test["instruction"], "attempts": 1, "goal": goal})
            attempt = index + 2
            model, state = self.folder / f"test-{attempt}.mjb", self.folder / f"test-{attempt}.npz"
            shutil.copyfile(test["model_path"], model)
            np.savez(state, qpos=test["qpos"], qvel=test["qvel"], ctrl=test["ctrl"])
            self.jobs.append(
                {
                    "instruction": test["instruction"],
                    "goal": goal,
                    "attempt": attempt,
                    "index": index,
                    "scene_number": test["test"],
                    "scene": (model, state),
                    "environment": None,
                    "env_label": test["change"],
                    "max_seconds": test["max_seconds"],
                    "name": f"Test {test['test']}" + (" (control)" if test["kind"] == "control" else ""),
                }
            )
        self.workers = Workers(task, env, self.jobs, self.folder, tests[0]["objects"])
        current = self


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
        ready = [{"attempt": 1, "task": 1, "scene": 1, "where": "live", "label": "", "env": "Live scene", "state": "ready"}]
        return ready + [
            {"attempt": e.attempt, "task": 1, "scene": e.attempt, "where": "hidden", "label": "", "env": e.label, "state": "ready"}
            for e in previews["envs"]
        ]
    return []


def publish_view(attempt, camera, view):
    """Tell the running parallel runs which run is watched and where the 3D camera is."""
    session = current
    if session is None or session.workers is None:
        return
    path = session.workers.view_path
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps({"attempt": attempt, "camera": camera, **view}))
        os.replace(temporary, path)
    except OSError:
        pass


def has_workers():
    """Whether the run tabs are showing a request's parallel runs, rather than prepared scenes."""
    return current is not None and current.workers is not None


def final_scene(attempt):
    """(model path, saved final state) of a finished parallel run, or None while it is running or has no state."""
    session = current
    if session is None or session.workers is None:
        return None
    for job in session.jobs:
        if job["attempt"] == attempt:
            path = session.folder / f"attempt-{attempt}.final.npz"
            return (job["scene"][0], path) if path.is_file() else None
    return None


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
                return ("preview", previews["version"], environment.version, camera), frame
    return None, None


def viewed_objects(attempt):
    """The object table of the parallel run the page is viewing, while its environment can be edited.
    @return {"view_objects", "view_selected", "view_editable"} for a prepared environment before a run, else {}.
    """
    if attempt < 2 or not previews or (current is not None and current.workers is not None):
        return {}
    for environment in previews["envs"]:
        if environment.attempt == attempt:
            return {"view_objects": environment.snapshot, "view_selected": environment.selected, "view_editable": True}
    return {}
