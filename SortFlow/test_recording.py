"""Offline checks for recording: no API call, no key."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

import mujoco
import numpy as np

import recording
import viewer_workspace_checked as checked
from yam_env import YamEnv


class RecordingTest(unittest.TestCase):
    """Videos hold the frames the run rendered, and the run itself is unchanged."""

    def setUp(self):
        """Open a fresh scene and a temporary folder."""
        self.env = YamEnv()
        self.home = np.r_[np.zeros(6), 1.0]
        self.folder = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Release the renderer."""
        self.env.close()

    def run_steps(self, count):
        """Step and render, as dream_control.capture does."""
        for _ in range(count):
            self.env.step(self.home, self.home)
            self.env.images()

    def test_attach_records_every_render_and_restores_the_environment(self):
        """Thirty renders give a 30-frame 640x360 video, and images() is the original afterwards."""
        path = self.folder / "attempt-1.mp4"
        attached = recording.attach(self.env, path)
        self.run_steps(30)
        attached.close()
        frames = recording.read_frames(path)
        self.assertEqual(len(frames), 30)
        self.assertEqual(frames[0].shape, (360, 640, 3))
        self.assertNotIn("images", self.env.__dict__)

    def test_grid_joins_runs_of_different_lengths(self):
        """Three clips of different lengths become one grid as long as the longest, with a label each."""
        entries = []
        for number, count in enumerate((10, 20, 15), 1):
            path = self.folder / f"attempt-{number}.mp4"
            attached = recording.attach(self.env, path)
            self.run_steps(count)
            attached.close()
            entries.append((path, f"attempt {number}", number != 2))
        entries.append((self.folder / "missing.mp4", "attempt 4", False))  # an unreadable clip is skipped
        written = recording.grid(entries, self.folder / "grid.mp4")
        self.assertEqual(written, 20)
        self.assertEqual(len(recording.read_frames(self.folder / "grid.mp4")), 20)

    def test_grid_with_nothing_to_join_writes_nothing(self):
        """No readable clip gives 0 frames and no file."""
        self.assertEqual(recording.grid([(self.folder / "none.mp4", "x", False)], self.folder / "grid.mp4"), 0)
        self.assertFalse((self.folder / "grid.mp4").exists())

    def test_a_checked_task_saves_its_video(self):
        """With recording on, one attempt leaves attempt-1.mp4 where the verdict says."""
        checked.RECORD, checked.HERE = True, self.folder

        def stub(argv, shared_env=None, stop_event=None, **kw):
            shared_env.data.joint("apple_free").qpos[:3] = [0.0, -0.04, 0.86]
            mujoco.mj_forward(shared_env.model, shared_env.data)
            for _ in range(300):
                if stop_event.is_set():
                    return
                shared_env.step(self.home, self.home)
                shared_env.images()
                time.sleep(0.005)

        original = checked._original
        checked._original = stub
        try:
            checked.checked_main(
                ["--task", "put apple into the bowl"], shared_env=self.env, stop_event=threading.Event()
            )
        finally:
            checked._original, checked.RECORD = original, False
        found = list((self.folder / "sortflow_results" / "videos").glob("*/attempt-1.mp4"))
        self.assertEqual(len(found), 1)
        self.assertGreater(len(recording.read_frames(found[0])), 5)


if __name__ == "__main__":
    unittest.main()
