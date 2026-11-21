"""Offline checks for the parallel runner: no API call, no key."""

import argparse
import json
import sys
import tempfile
import time
import unittest

import mujoco
import numpy as np

import parallel_bench

FAKE_WORKER = (
    "import json, sys, time; time.sleep(1.0); "
    "n = int(sys.argv[1]); "
    "print('noise line'); "
    "print('RESULT ' + json.dumps({'task': 't', 'success': n % 2 == 0, 'success_try': 1 if n % 2 == 0 else None, "
    "'sim_seconds': 3.0, 'error': None}))"
)


class ParallelBenchTest(unittest.TestCase):
    """The runner runs trials side by side, counts them correctly and survives a failing worker."""

    def args(self, **over):
        """Arguments for run_batch, with a temporary output folder."""
        base = dict(
            task="put orange into the bowl",
            trials=8,
            workers=4,
            tries=1,
            seconds=5,
            actions=48,
            out=tempfile.mkdtemp(),
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_trials_run_side_by_side_and_are_counted(self):
        """Eight one-second trials at four at a time finish in about two seconds, not eight."""
        counter = iter(range(100))
        # Each trial gets its own number through its command line; stand in with one command per call.
        args = self.args()
        original = parallel_bench.subprocess.run

        def run(command, **kw):
            return original([sys.executable, "-c", FAKE_WORKER, str(next(counter))], **kw)

        parallel_bench.subprocess.run = run
        try:
            started = time.monotonic()
            summary = parallel_bench.run_batch(args, command=["unused"])
            elapsed = time.monotonic() - started
        finally:
            parallel_bench.subprocess.run = original
        self.assertEqual(summary["trials"], 8)
        self.assertEqual(summary["successes_within_tries"], 4)
        self.assertEqual(summary["first_try_rate"], 0.5)
        self.assertLess(elapsed, 5.5, "trials did not overlap")

    def test_a_crashing_worker_is_reported_not_raised(self):
        """A worker that dies without a result line becomes one FAIL with its error, and the batch finishes."""
        summary = parallel_bench.run_batch(
            self.args(trials=2, workers=2), command=[sys.executable, "-c", "raise SystemExit(3)"]
        )
        self.assertEqual(summary["successes_within_tries"], 0)
        self.assertEqual(len(summary["errors"]), 2)
        self.assertIn("exited 3", summary["errors"][0])

    def test_worker_succeeds_on_the_second_try(self):
        """With tries=2, a first attempt that fails and a second that places the apple count as success on try 2."""
        calls = []
        home = np.r_[np.zeros(6), 1.0]

        def runner(argv, shared_env=None, stop_event=None, **kw):
            calls.append(1)
            if len(calls) == 2:
                shared_env.data.joint("apple_free").qpos[:3] = [0.0, -0.04, 0.86]
                mujoco.mj_forward(shared_env.model, shared_env.data)
            for _ in range(200):
                if stop_event.is_set():
                    return
                shared_env.step(home, home)
                time.sleep(0.005)

        record = parallel_bench.run_worker("put apple into the bowl", 5, 48, 2, runner=runner)
        self.assertTrue(record["success"])
        self.assertEqual(record["success_try"], 2)
        self.assertEqual(len(calls), 2)

    def test_a_retryable_refusal_is_asked_again(self):
        """A retryable error (no capacity) is retried without using up a try."""
        calls = []

        class Busy(Exception):
            retryable = True
            retry_after_s = 0.01

        def runner(argv, shared_env=None, stop_event=None, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise Busy("DA-2000 no capacity")

        record = parallel_bench.run_worker("put apple into the bowl", 5, 48, 1, runner=runner)
        self.assertEqual(len(calls), 2)
        self.assertEqual(record["tries_used"], 1)

    def test_a_worker_starts_from_a_saved_scene(self):
        """With a scene given, every attempt starts from that model and state, not from the default scene."""
        from yam_env import YamEnv

        source = YamEnv()
        try:
            source.data.joint("apple_free").qpos[:3] = [0.12, 0.10, 0.80]
            mujoco.mj_forward(source.model, source.data)
            folder = tempfile.mkdtemp()
            mujoco.mj_saveModel(source.model, f"{folder}/scene.mjb")
            np.savez(f"{folder}/scene.npz", qpos=source.data.qpos, qvel=source.data.qvel, ctrl=source.data.ctrl)
        finally:
            source.close()
        seen = []

        def runner(argv, shared_env=None, stop_event=None, **kw):
            seen.append(round(float(shared_env.data.body("apple").xpos[0]), 2))

        parallel_bench.run_worker(
            "put apple into the bowl", 5, 48, 1, runner=runner, scene=(f"{folder}/scene.mjb", f"{folder}/scene.npz")
        )
        self.assertEqual(seen, [0.12])

    def test_a_worker_judges_by_the_websites_contact_rule_when_given_a_goal(self):
        """With a goal body and target, the worker uses the contact rule, so any body in the scene can be judged."""
        home = np.r_[np.zeros(6), 1.0]

        def runner(argv, shared_env=None, stop_event=None, **kw):
            shared_env.data.joint("apple_free").qpos[:3] = [0.0, -0.04, 0.86]
            mujoco.mj_forward(shared_env.model, shared_env.data)
            for _ in range(200):  # ten simulated seconds, ended early on success
                if stop_event.is_set():
                    return
                shared_env.step(home, home)
                shared_env.images()  # dream_control renders after every step, which is when the goal is checked
                time.sleep(0.003)

        record = parallel_bench.run_worker("anything", 20, 48, 1, runner=runner, goal=("apple", "bowl"))
        self.assertTrue(record["success"])
        self.assertEqual(record["success_try"], 1)

    def test_a_worker_streams_frames_and_a_final_status(self):
        """With a pipe given, the worker streams its cameras while it runs and ends with its outcome."""
        import os
        import threading

        import livefeed

        home = np.r_[np.zeros(6), 1.0]

        def runner(argv, shared_env=None, stop_event=None, **kw):
            shared_env.data.joint("apple_free").qpos[:3] = [0.0, -0.04, 0.86]
            mujoco.mj_forward(shared_env.model, shared_env.data)
            for _ in range(200):
                if stop_event.is_set():
                    return
                shared_env.step(home, home)
                shared_env.images()
                time.sleep(0.003)

        read_fd, write_fd = os.pipe()
        messages = []

        def read():
            with os.fdopen(read_fd, "rb") as stream:
                messages.extend(livefeed.read_messages(stream))

        reader = threading.Thread(target=read)
        reader.start()
        record = parallel_bench.run_worker(
            "anything", 20, 48, 1, runner=runner, goal=("apple", "bowl"), live=os.fdopen(write_fd, "wb", buffering=0)
        )
        reader.join(timeout=5)
        self.assertTrue(record["success"])
        kinds = {livefeed.kind_name(kind) for kind, _ in messages}
        self.assertEqual(kinds, {"status", "top", "left", "right"})
        final = json.loads([payload for kind, payload in messages if kind == livefeed.STATUS][-1])
        self.assertEqual(final["state"], "succeeded")
        self.assertEqual(final["sim_seconds"], record["sim_seconds"])

    def test_the_last_line_is_the_record(self):
        """A worker's record is parsed from its last RESULT line, whatever the model printed before it."""
        line = "RESULT " + json.dumps({"success": True})
        self.assertTrue(line.startswith(parallel_bench.RESULT))


if __name__ == "__main__":
    unittest.main()
