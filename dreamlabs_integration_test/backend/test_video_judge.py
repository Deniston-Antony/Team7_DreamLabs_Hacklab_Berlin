"""Offline checks for the video judge: real MP4 files, Claude replaced by a stand-in. No API call, no key.

    python -m unittest test_video_judge
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

import video_judge


def write_video(path, frames=60, colour_change_at=None):
    """An MP4 whose frames are numbered by brightness; after `colour_change_at` they turn green."""
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 20, (160, 120))
    for i in range(frames):
        frame = np.full((120, 160, 3), (i * 4) % 256, np.uint8)
        if colour_change_at is not None and i >= colour_change_at:
            frame[:, :] = (0, 255, 0)
        out.write(frame)
    out.release()


def fake_claude(verdicts):
    """A stand-in for ask_claude that returns verdicts by instruction and records what it was sent."""
    calls = []

    def ask(instruction, frames):
        calls.append((instruction, len(frames)))
        return {"verdict": verdicts.get(instruction, "UNSURE"), "confidence": 0.9, "reason": "seen in the last frame"}

    ask.calls = calls
    return ask


class VideoJudgeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def task(self, task_id="t1", **extra):
        (self.root / task_id).mkdir()
        return {"task_id": task_id, "instruction": "put the apple in the bowl", "status": "COMPLETED", **extra}

    def test_samples_lean_on_the_end(self):
        write_video(self.root / "v.mp4", frames=100, colour_change_at=85)
        frames = [cv2.imdecode(np.frombuffer(f, np.uint8), cv2.IMREAD_COLOR) for f in video_judge.sample_frames(self.root / "v.mp4")]
        self.assertEqual(len(frames), video_judge.FRAMES)
        green = [f for f in frames if f[60, 80, 1] > 200 and f[60, 80, 0] < 60]
        self.assertGreaterEqual(len(green), video_judge.FRAMES // 2)  # the ending is well covered
        self.assertTrue(np.array_equal(frames[-1], green[-1]))  # the very last frame is included

    def test_unreadable_video_is_skipped(self):
        (self.root / "bad.mp4").write_bytes(b"not a video")
        result = video_judge.judge_video("x", self.root / "bad.mp4", True, ask=fake_claude({}))
        self.assertEqual(result["verdict"], "SKIPPED")

    def test_flags_disagreement_with_the_simulator(self):
        task = self.task(outcome="SUCCESS")
        write_video(self.root / "t1" / "attempt-1.mp4")
        task["video_judge"] = {"status": "RUNNING", "attempts": []}
        video_judge.judge_task(task, self.root / "t1", ask=fake_claude({"put the apple in the bowl": "FAIL"}))
        state = task["video_judge"]
        self.assertEqual(state["status"], "DONE")
        self.assertFalse(state["attempts"][0]["agrees_with_sim"])
        self.assertEqual(state["disagreements"], 1)

    def test_each_run_is_judged_against_its_own_task(self):
        plan = [{"instruction": "put the apple in the bowl", "attempts": 1},
                {"instruction": "put the banana on the plate", "attempts": 2}]
        results = [{"attempt": 1, "task": 1, "success": True}, {"attempt": 2, "task": 2, "success": True},
                   {"attempt": 3, "task": 2, "success": False}]
        task = self.task(plan=plan, attempt_results=results)
        for n in (1, 2, 3, 10):
            write_video(self.root / "t1" / f"attempt-{n}.mp4")
        task["video_judge"] = {"status": "RUNNING", "attempts": []}
        ask = fake_claude({"put the apple in the bowl": "PASS", "put the banana on the plate": "PASS"})
        video_judge.judge_task(task, self.root / "t1", ask=ask)
        sent = [instruction for instruction, _ in ask.calls]
        self.assertEqual(sent[:3], ["put the apple in the bowl", "put the banana on the plate", "put the banana on the plate"])
        self.assertEqual([a["video"] for a in task["video_judge"]["attempts"]], ["attempt-1", "attempt-2", "attempt-3", "attempt-10"])
        self.assertEqual([a.get("agrees_with_sim") for a in task["video_judge"]["attempts"][:3]], [True, True, False])

    def test_one_failing_video_does_not_stop_the_rest(self):
        task = self.task()
        write_video(self.root / "t1" / "attempt-1.mp4")
        write_video(self.root / "t1" / "attempt-2.mp4")
        calls = []

        def ask(instruction, frames):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("rate limited")
            return {"verdict": "PASS", "confidence": 1.0, "reason": "ok"}

        task["video_judge"] = {"status": "RUNNING", "attempts": []}
        video_judge.judge_task(task, self.root / "t1", ask=ask)
        self.assertEqual([a["verdict"] for a in task["video_judge"]["attempts"]], ["ERROR", "PASS"])

    def test_submit_returns_at_once_and_judges_in_the_background(self):
        task = self.task()
        write_video(self.root / "t1" / "attempt-1.mp4")
        release = threading.Event()

        def slow(instruction, frames):
            release.wait(5)
            return {"verdict": "PASS", "confidence": 1.0, "reason": "ok"}

        with mock.patch.object(video_judge, "_worker", None):
            started = time.monotonic()
            video_judge.submit(task, self.root, ask=slow)
            self.assertLess(time.monotonic() - started, 0.5)  # the robot is free right away
            self.assertIn(task["video_judge"]["status"], ("QUEUED", "RUNNING"))
            release.set()
            for _ in range(50):
                if task["video_judge"]["status"] == "DONE":
                    break
                time.sleep(0.1)
        self.assertEqual(task["video_judge"]["status"], "DONE")

    def test_callbacks_save_each_verdict_and_only_limits_the_videos(self):
        task = self.task()
        for number in (1, 2):
            write_video(self.root / "t1" / f"attempt-{number}.mp4")
        saved, finished = [], threading.Event()
        video_judge.submit(task, self.root, ask=fake_claude({"put the apple in the bowl": "PASS"}), on_attempt=lambda n, v: saved.append((n, v["verdict"])), on_done=lambda state: finished.set(), only={2})
        self.assertTrue(finished.wait(5))
        self.assertEqual(saved, [(2, "PASS")])

    def test_no_credentials_or_cancelled_is_skipped(self):
        with mock.patch.object(video_judge, "has_credentials", return_value=False):
            task = self.task()
            video_judge.submit(task, self.root)
            self.assertEqual(task["video_judge"]["status"], "SKIPPED")
            self.assertIn("ANTHROPIC_API_KEY", task["video_judge"]["reason"])
        task = self.task("t2", status="CANCELLED")
        video_judge.submit(task, self.root, ask=fake_claude({}))
        self.assertEqual(task["video_judge"]["status"], "SKIPPED")


if __name__ == "__main__":
    unittest.main()
