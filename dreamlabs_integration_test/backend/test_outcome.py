"""Offline checks for task videos and attempts at once behind /api/tasks: no API call, no key.

Dream-1 is replaced by a stand-in that moves an object, so the simulator, the goal check, the recording and the
endpoints are the real ones. Run from this folder with the backend's dependencies on the path:

    MUJOCO_GL=egl PYTHONPATH=.deps python -m unittest test_outcome
"""

import os
import tempfile

os.environ.setdefault("DREAM_EVALUATION_DATA", tempfile.mkdtemp(prefix="dream-evaluation-tests-"))

import textwrap
import time
import unittest
from pathlib import Path

import io
import json

import main
import mujoco
import numpy as np
import outcome
from fastapi.testclient import TestClient
from PIL import Image

HOME = np.r_[np.zeros(6), 1.0]


def put(env, body, position):
    """Teleport a free body to a position."""
    env.data.joint(f"{body}_free" if body != "water_bottle" else "bottle_free").qpos[:3] = position
    mujoco.mj_forward(env.model, env.data)


def model(apple_into_bowl=False, steps=60):
    """A stand-in for dream_control.main: optionally drops the apple in the bowl, then steps until stopped."""

    def run(argv, shared_env=None, stop_event=None, **kw):
        if apple_into_bowl:
            put(shared_env, "apple", [0.0, -0.04, 0.86])
        for _ in range(steps):
            if stop_event.is_set():
                return
            shared_env.step(HOME, HOME)
            shared_env.images()  # the backend's goal check runs on every render
            time.sleep(0.003)

    return run


class OutcomeTest(unittest.TestCase):
    """A task through the real endpoints ends with videos, and with attempts at once, with their results."""

    @classmethod
    def setUpClass(cls):
        """One client; videos go to a temporary folder."""
        cls.client = TestClient(main.app)
        cls.keep = (outcome.VIDEOS, main.dc.main, outcome.SORTFLOW)
        outcome.VIDEOS = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        """Put back what the tests replaced."""
        outcome.VIDEOS, main.dc.main, outcome.SORTFLOW = cls.keep

    def setUp(self):
        """Every task starts from the default scene, with no saved layout."""
        main.saved_layout = None
        self.client.post("/api/scene/reset")

    def tearDown(self):
        """A failed test must not leave a task running for the next one."""
        if main.active_task is not None:
            main.active_stop.set()
            self.wait(main.active_task["task_id"])

    def submit(self, instruction, runner, **body):
        """Start a task with a stand-in model and wait for its final state."""
        main.dc.main = runner
        payload = {"max_seconds": 20, **body}
        if instruction is not None:
            payload["instruction"] = instruction
        reply = self.client.post("/api/tasks/execute", json=payload)
        self.assertEqual(reply.status_code, 202, reply.text)
        return self.wait(reply.json()["task_id"])

    def wait(self, task_id):
        """The task's status once it is no longer running."""
        for _ in range(600):
            task = self.client.get(f"/api/tasks/{task_id}/status").json()
            if task["status"] != "RUNNING":
                return task
            time.sleep(0.05)
        self.fail("the task never finished")

    def fake_workers(self, body):
        """A folder holding a stand-in parallel_bench.py that loads the saved scene, records and reports."""
        folder = Path(tempfile.mkdtemp())
        (folder / "parallel_bench.py").write_text(
            textwrap.dedent(
                f"""
                import json, sys
                import mujoco, numpy as np
                sys.path.insert(0, {str(self.keep[2])!r})
                import recording
                from yam_env import YamEnv
                arg = lambda name: sys.argv[sys.argv.index(name) + 1]
                obj, tgt, said = arg("--goal-object"), arg("--goal-target"), arg("--task")
                env = YamEnv()
                env.model = mujoco.MjModel.from_binary_path(arg("--scene-model"))
                env.data = mujoco.MjData(env.model)
                start = np.load(arg("--scene-state"))
                env.data.qpos[:] = start["qpos"]
                mujoco.mj_forward(env.model, env.data)
                home = np.r_[np.zeros(6), 1.0]
                clip = recording.attach(env, arg("--record"))
                for _ in range(10):
                    env.step(home, home)
                    env.images()
                clip.close()
                x_cm = round(float(env.data.body("apple").xpos[0]) * 100)
{textwrap.indent(body, " " * 16)}
                """
            )
        )
        return folder

    def test_a_suite_runs_different_tasks_from_one_scene_copy(self):
        """Three tasks, four attempts in all: every worker gets its own task and goal, and results are per task."""
        outcome.SORTFLOW = self.fake_workers(
            'idx = ["apple", "orange", "banana"].index(obj)\n'
            'print("RESULT " + json.dumps({"success": obj == "orange", "sim_seconds": 5.0, "closest_cm": idx * 10}))'
        )
        try:
            task = self.submit(
                None,
                model(steps=20),
                tasks=[
                    {"instruction": "put apple on the plate", "attempts": 2},
                    {"instruction": "put orange into the bowl"},
                    {"instruction": "put banana on the plate"},
                ],
            )
        finally:
            outcome.SORTFLOW = self.keep[2]
        self.assertEqual(task["attempts"], 4)
        hidden = [r for r in task["attempt_results"] if r["where"] == "hidden"]
        self.assertEqual([(r["task"], r["closest_cm"]) for r in hidden], [(1, 0), (2, 10), (3, 20)])
        self.assertEqual([r["success"] for r in hidden], [False, True, False])
        groups = task["task_results"]
        self.assertEqual([g["instruction"] for g in groups], [t["instruction"] for t in task["plan"]])
        self.assertEqual([g["passed"] for g in groups], [False, True, False])
        self.assertEqual(task["tasks_succeeded"], 1)
        self.assertEqual(task["videos"], ["attempt-1", "attempt-2", "attempt-3", "attempt-4", "grid"])
        self.assertEqual(self.client.get(f"/api/tasks/{task['task_id']}/video/grid").status_code, 200)

    def test_a_suite_with_a_line_the_checker_cannot_judge_is_refused_before_anything_runs(self):
        """The 422 names the line, and the backend is free afterwards."""
        reply = self.client.post(
            "/api/tasks/execute",
            json={"tasks": [{"instruction": "put apple on the plate"}, {"instruction": "wave hello"}]},
        )
        self.assertEqual(reply.status_code, 422)
        self.assertIn("task 2", reply.text)
        self.assertIn("wave hello", reply.text)
        self.assertEqual(self.client.post("/api/scene/reset").status_code, 200, "no task was started")

    def test_a_suite_is_capped_and_must_be_unambiguous(self):
        """More than 8 attempts in all, both forms at once, and an empty request are refused."""
        too_many = {"tasks": [{"instruction": "put apple on the plate", "attempts": 5}] * 2}
        self.assertEqual(self.client.post("/api/tasks/execute", json=too_many).status_code, 422)
        both = {"instruction": "put apple on the plate", "tasks": [{"instruction": "put apple on the plate"}]}
        self.assertEqual(self.client.post("/api/tasks/execute", json=both).status_code, 422)
        self.assertEqual(self.client.post("/api/tasks/execute", json={}).status_code, 422)

    def test_cancelling_a_suite_ends_its_workers(self):
        """Stop task ends the hidden workers too, not only the live attempt."""
        pidfile = Path(tempfile.mkdtemp()) / "pid"
        outcome.SORTFLOW = self.fake_workers(
            f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)"
        )
        try:
            main.dc.main = model(steps=40)
            task_id = self.client.post(
                "/api/tasks/execute",
                json={
                    "tasks": [{"instruction": "put apple on the plate"}, {"instruction": "put orange into the bowl"}],
                    "max_seconds": 20,
                },
            ).json()["task_id"]
            for _ in range(200):  # the live attempt ends, then the task waits for the worker
                if self.client.get(f"/api/tasks/{task_id}/status").json().get("waiting_for_attempts"):
                    break
                time.sleep(0.05)
            for _ in range(100):
                if pidfile.exists():
                    break
                time.sleep(0.1)
            pid = int(pidfile.read_text())
            self.client.post(f"/api/tasks/{task_id}/cancel", json={})
            self.assertEqual(self.wait(task_id)["status"], "CANCELLED")
            time.sleep(1)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            outcome.SORTFLOW = self.keep[2]

    def streaming_workers(self, steps=120, state="succeeded"):
        """A folder holding a stand-in parallel_bench.py that streams its cameras over --live-fd, as a worker does."""
        folder = Path(tempfile.mkdtemp())
        (folder / "parallel_bench.py").write_text(
            textwrap.dedent(
                f"""
                import json, os, sys, time
                sys.path.insert(0, {str(self.keep[2])!r})
                import numpy as np
                import livefeed
                from yam_env import YamEnv
                arg = lambda name: sys.argv[sys.argv.index(name) + 1]
                env = YamEnv()
                feed = livefeed.attach(env, os.fdopen(int(arg("--live-fd")), "wb", buffering=0), every=2)
                home = np.r_[np.zeros(6), 1.0]
                for _ in range({steps}):
                    env.step(home, home)
                    env.images()
                    time.sleep(0.02)
                feed.status({state!r}, sim_seconds=3.0)
                feed.close()
                print("RESULT " + json.dumps({{"success": {state!r} == "succeeded", "sim_seconds": 3.0}}))
                """
            )
        )
        return folder

    @staticmethod
    def receive(ws, want, tries=400):
        """The next websocket message of a kind: `text` (parsed as JSON) or `bytes`."""
        for _ in range(tries):
            message = ws.receive()
            if message.get(want) is not None:
                return json.loads(message["text"]) if want == "text" else message["bytes"]
        raise AssertionError(f"no {want} message arrived")

    def test_a_hidden_worker_can_be_watched_live_and_the_live_view_comes_back(self):
        """The page asks for attempt 2 and gets that worker's pictures; asking for attempt 1 gets the simulator."""
        outcome.SORTFLOW = self.streaming_workers(steps=150)
        self.client.post("/api/scene/reset")
        main.dc.main = model(steps=20)
        reply = self.client.post(
            "/api/tasks/execute", json={"instruction": "put apple into the bowl", "max_seconds": 20, "attempts": 2}
        )
        task_id = reply.json()["task_id"]
        try:
            with self.client.websocket_connect("/ws/live", headers={"origin": "http://127.0.0.1:5173"}) as ws:
                ws.send_json({"attempt": 2})
                frame = self.receive(ws, "bytes")
                self.assertEqual(frame[:2], b"\xff\xd8", "a JPEG")
                self.assertEqual(Image.open(io.BytesIO(frame)).size, (640, 360))
                state = self.receive(ws, "text")
                while state["attempt"] != 2 or len(state["workers"]) < 2 or state["workers"][1]["state"] != "running":
                    state = self.receive(ws, "text")  # the first states can predate the worker's first frame
                self.assertEqual([w["where"] for w in state["workers"]], ["live", "hidden"])
                self.assertEqual(state["workers"][1]["label"], "apple>bowl")
                ws.send_json({"camera": "free"})  # a worker has no free 3D camera: it shows the top one instead
                self.assertEqual(self.receive(ws, "bytes")[:2], b"\xff\xd8")
                ws.send_json({"attempt": 1, "camera": "top"})
                state = self.receive(ws, "text")
                while state["attempt"] != 1:
                    state = self.receive(ws, "text")
                self.assertEqual(self.receive(ws, "bytes")[:2], b"\xff\xd8")
            task = self.wait(task_id)
            self.assertEqual(task["attempt_results"][1]["success"], True)
            states = outcome.attempt_states()
            self.assertEqual([w["state"] for w in states], ["finished", "succeeded"])
        finally:
            outcome.SORTFLOW = self.keep[2]

    def test_a_worker_that_stops_without_reporting_is_shown_as_ended(self):
        """No final status and no frames from a worker that dies at once: its dropdown entry says ended."""
        outcome.SORTFLOW = self.fake_workers("raise SystemExit(3)")
        try:
            self.submit("put apple into the bowl", model(steps=20), attempts=2)
            self.assertEqual([w["state"] for w in outcome.attempt_states()], ["finished", "ended"])
        finally:
            outcome.SORTFLOW = self.keep[2]

    def test_watching_reads_whole_messages_and_falls_back_to_the_top_camera(self):
        """The reader keeps the newest frame per camera and the latest status, and any other camera shows top."""
        import livefeed

        watching = outcome.Watching({"attempt": 2})
        stream = io.BytesIO(
            livefeed.pack(livefeed.STATUS, b'{"state": "running"}')
            + livefeed.pack(1, b"top-1")
            + livefeed.pack(1, b"top-2")
            + livefeed.pack(2, b"left-1")
            + livefeed.pack(livefeed.STATUS, b"not json")
        )
        watching.read(stream)
        self.assertEqual(watching.frames, {"top": b"top-2", "left": b"left-1"})
        self.assertEqual(watching.status, {"state": "running"})
        self.assertEqual(watching.seq, 3)

        class Fake:
            workers = type("W", (), {"watching": {2: watching}})()

        keep, outcome.current = outcome.current, Fake()
        try:
            self.assertEqual(outcome.worker_frame(2, "left"), (3, b"left-1"))
            self.assertEqual(outcome.worker_frame(2, "free"), (3, b"top-2"))
            self.assertEqual(outcome.worker_frame(5, "top"), (None, None))
        finally:
            outcome.current = keep

    def endless_workers(self):
        """A folder holding a stand-in parallel_bench.py that runs the real worker code on a run that never ends."""
        folder = Path(tempfile.mkdtemp())
        (folder / "parallel_bench.py").write_text(
            textwrap.dedent(
                f"""
                import json, os, sys, time
                sys.path.insert(0, {str(self.keep[2])!r})
                import numpy as np
                import parallel_bench as real

                arg = lambda name: sys.argv[sys.argv.index(name) + 1]
                home = np.r_[np.zeros(6), 1.0]

                def runner(argv, shared_env=None, stop_event=None, **kw):
                    while True:
                        shared_env.step(home, home)
                        shared_env.images()
                        time.sleep(0.02)

                real.stop_on_sigterm()
                try:
                    record = real.run_worker(
                        arg("--task"), 60, 48, 1, runner=runner, video=arg("--record"),
                        scene=(arg("--scene-model"), arg("--scene-state")),
                        goal=(arg("--goal-object"), arg("--goal-target")),
                        live=os.fdopen(int(arg("--live-fd")), "wb", buffering=0),
                    )
                except real.StopRequested:
                    record = {{"success": False, "error": "stopped", "stopped": True}}
                print("RESULT " + json.dumps(record), flush=True)
                """
            )
        )
        return folder

    def objects(self):
        """The scene's objects by name, as the backend reports them."""
        return {o["object_id"]: o for o in main.feed.objects}

    def test_reset_during_a_run_stops_every_run_keeps_their_videos_and_resets(self):
        """Reset scene mid-run: the live run and the parallel runs stop cleanly, then the scene is reset."""
        outcome.SORTFLOW = self.endless_workers()
        try:
            self.client.post("/api/objects/move", json={"object_id": "apple", "position": {"x": 0.2, "y": 0.18, "z": 0.8}})
            main.dc.main = model(steps=100000)
            task_id = self.client.post(
                "/api/tasks/execute",
                json={"tasks": [{"instruction": "put apple into the bowl"}, {"instruction": "put orange into the bowl"}]},
            ).json()["task_id"]
            for _ in range(200):  # until the parallel run streams frames
                if [w["state"] for w in outcome.attempt_states()] == ["running", "running"]:
                    break
                time.sleep(0.1)
            reply = self.client.post("/api/scene/reset")
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertTrue(reply.json()["stopped_task"])
            task = self.client.get(f"/api/tasks/{task_id}/status").json()
            self.assertEqual(task["status"], "CANCELLED")
            hidden = task["attempt_results"][1]
            self.assertEqual((hidden["stopped"], hidden["error"]), (True, "stopped"))
            self.assertEqual(outcome.attempt_states()[1]["state"], "stopped")
            self.assertTrue(all(proc.poll() is not None for proc in outcome.current.workers.procs), "no run left")
            self.assertIn("attempt-2", task["videos"])
            video = self.client.get(f"/api/tasks/{task_id}/video/attempt-2")
            self.assertEqual(video.status_code, 200)
            apple = self.objects()["apple"]["position"]
            self.assertLess(abs(apple[0] - 0.15) + abs(apple[1] - 0.105), 0.02, "the apple is back where it starts")
        finally:
            outcome.SORTFLOW = self.keep[2]

    def test_reset_returns_to_a_saved_layout_until_it_is_forgotten(self):
        """Save layout keeps moved, removed and added objects; Reset puts them back; forgetting returns to default."""
        move = lambda x, y: self.client.post(  # noqa: E731
            "/api/objects/move", json={"object_id": "apple", "position": {"x": x, "y": y, "z": 0.8}}
        )
        move(0.05, 0.18)
        self.client.post("/api/objects/remove", json={"object_id": "banana"})
        block = self.client.post("/api/objects/add", json={"type": "block", "position": {"x": -0.1, "y": 0.15, "z": 0.8}})
        self.assertEqual(self.client.post("/api/scene/layout").status_code, 200)
        move(0.25, -0.1)
        self.assertTrue(self.client.post("/api/scene/reset").json()["layout"])
        objects = self.objects()
        self.assertLess(abs(objects["apple"]["position"][0] - 0.05) + abs(objects["apple"]["position"][1] - 0.18), 0.02)
        self.assertNotIn("banana", objects)
        self.assertIn(block.json()["object_id"], objects)
        self.assertEqual(self.client.delete("/api/scene/layout").status_code, 200)
        self.assertFalse(self.client.post("/api/scene/reset").json()["layout"])
        objects = self.objects()
        self.assertIn("banana", objects)
        self.assertLess(abs(objects["apple"]["position"][0] - 0.15) + abs(objects["apple"]["position"][1] - 0.105), 0.02)

    def test_a_layout_cannot_be_saved_while_a_task_runs(self):
        """Saving reads the scene, which is moving during a run, so it waits for the task to end."""
        main.dc.main = model(steps=4000)
        self.client.post("/api/tasks/execute", json={"instruction": "put apple into the bowl", "max_seconds": 20})
        time.sleep(0.3)
        self.assertEqual(self.client.post("/api/scene/layout").status_code, 409)

    def test_success_keeps_the_backends_verdict_and_saves_a_video(self):
        """The apple settling in the bowl is COMPLETED / SUCCESS as before, now with a video the API serves."""
        task = self.submit("put apple into the bowl", model(apple_into_bowl=True, steps=400))
        self.assertEqual((task["status"], task["outcome"]), ("COMPLETED", "SUCCESS"))
        self.assertEqual(task["videos"], ["attempt-1"])
        video = self.client.get(f"/api/tasks/{task['task_id']}/video/attempt-1")
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.headers["content-type"], "video/mp4")
        self.assertGreater(len(video.content), 1000)

    def test_a_failed_task_still_has_a_video(self):
        """Failures are recorded too: the evidence for what went wrong."""
        task = self.submit("put orange into the bowl", model())
        self.assertEqual((task["status"], task["outcome"]), ("TIMED_OUT", "FAILURE"))
        self.assertEqual(task["videos"], ["attempt-1"])

    def test_attempts_run_from_a_copy_of_the_edited_scene(self):
        """Three attempts: the live one plus two workers that start where the user left the apple, and a grid."""
        outcome.SORTFLOW = self.fake_workers(
            'assert (obj, tgt) == ("apple", "bowl") and said == "put apple into the bowl"\n'
            'print("RESULT " + json.dumps({"success": x_cm == 12, "sim_seconds": 4.0, "closest_cm": x_cm}))'
        )
        try:
            main.sim.env.reset()
            put(main.sim.env, "apple", [0.12, 0.10, 0.80])  # the user dragged the apple: workers must start there
            main.sim.env.step(HOME, HOME)
            task = self.submit("put apple into the bowl", model(steps=20), attempts=3)
        finally:
            outcome.SORTFLOW = self.keep[2]
        results = task["attempt_results"]
        self.assertEqual([r["where"] for r in results], ["live", "hidden", "hidden"])
        self.assertEqual([r["closest_cm"] for r in results[1:]], [12, 12])
        self.assertEqual(task["attempts_succeeded"], 2)
        self.assertEqual(task["outcome"], "FAILURE", "the live attempt's own verdict is not rewritten")
        self.assertEqual(task["videos"], ["attempt-1", "attempt-2", "attempt-3", "grid"])
        self.assertEqual(self.client.get(f"/api/tasks/{task['task_id']}/video/grid").status_code, 200)

    def test_a_crashing_worker_is_reported_not_raised(self):
        """A worker that dies without a result becomes a failed attempt with its error; the task still ends."""
        outcome.SORTFLOW = self.fake_workers("raise SystemExit(3)")
        try:
            task = self.submit("put apple into the bowl", model(steps=20), attempts=2)
        finally:
            outcome.SORTFLOW = self.keep[2]
        self.assertEqual(task["attempt_results"][1]["success"], False)
        self.assertIn("worker exited", task["attempt_results"][1]["error"])

    def test_attempts_need_an_instruction_the_checker_understands(self):
        """With no goal to judge, hidden attempts are skipped and the task says so."""
        task = self.submit("wave hello", model(), attempts=3)
        self.assertEqual(task["outcome"], "NOT_EVALUATED")
        self.assertIn("cannot be checked", task["attempts_note"])
        self.assertNotIn("attempt_results", task)

    def test_cancel_ends_the_task(self):
        """A user's cancel gives CANCELLED."""
        main.dc.main = model(steps=4000)
        task_id = self.client.post(
            "/api/tasks/execute", json={"instruction": "put apple into the bowl", "max_seconds": 20}
        ).json()["task_id"]
        time.sleep(0.5)
        self.client.post(f"/api/tasks/{task_id}/cancel", json={})
        self.assertEqual(self.wait(task_id)["status"], "CANCELLED")

    def test_a_video_problem_does_not_leave_the_task_running(self):
        """If finishing the session raises, the task still ends and reports the problem."""
        original = outcome.Session.finish

        def broken(self, result):
            raise RuntimeError("disk full")

        outcome.Session.finish = broken
        try:
            task = self.submit("put orange into the bowl", model())
        finally:
            outcome.Session.finish = original
        self.assertIn("disk full", task["attempts_error"])
        self.assertEqual(self.client.post("/api/scene/reset").status_code, 200, "the backend is free again")

    def test_bad_requests_are_refused(self):
        """Too many attempts, and video names that try to leave the folder, are refused."""
        reply = self.client.post("/api/tasks/execute", json={"instruction": "put apple into the bowl", "attempts": 9})
        self.assertEqual(reply.status_code, 422)
        self.assertEqual(self.client.get("/api/tasks/nope/video/grid").status_code, 404)
        task = self.submit("put apple into the bowl", model(apple_into_bowl=True, steps=400))
        self.assertEqual(self.client.get(f"/api/tasks/{task['task_id']}/video/..%2Fx").status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{task['task_id']}/video/attempt-9").status_code, 404)


if __name__ == "__main__":
    unittest.main()
