"""Offline checks for task videos and attempts at once behind /api/tasks: no API call, no key.

Dream-1 is replaced by a stand-in that moves an object, so the simulator, the goal check, the recording and the
endpoints are the real ones. Run from this folder with the backend's dependencies on the path:

    MUJOCO_GL=egl PYTHONPATH=.deps python -m unittest test_outcome
"""

import os
import tempfile

os.environ.setdefault("DREAM_VISION", "off")
os.environ.setdefault("DREAM_EVALUATION_DATA", tempfile.mkdtemp(prefix="dream-evaluation-tests-"))

import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import main
import mujoco
import numpy as np
import outcome
from fastapi.testclient import TestClient

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
        """Every task starts from the default scene."""
        self.client.post("/api/scene/reset")

    def submit(self, instruction, runner, **body):
        """Start a task with a stand-in model and wait for its final state."""
        main.dc.main = runner
        reply = self.client.post("/api/tasks/execute", json={"instruction": instruction, "max_seconds": 20, **body})
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
                assert arg("--goal-object") == "apple" and arg("--goal-target") == "bowl", sys.argv
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
                {body}
                """
            )
        )
        return folder

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
        self.assertIn("one attempt", task["attempts_note"])
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
        reply = self.client.post("/api/tasks/execute", json={"instruction": "put apple into the bowl", "attempts": 10})
        self.assertEqual(reply.status_code, 422)
        self.assertEqual(self.client.get("/api/tasks/nope/video/grid").status_code, 404)
        task = self.submit("put apple into the bowl", model(apple_into_bowl=True, steps=400))
        self.assertEqual(self.client.get(f"/api/tasks/{task['task_id']}/video/..%2Fx").status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{task['task_id']}/video/attempt-9").status_code, 404)


if __name__ == "__main__":
    unittest.main()
