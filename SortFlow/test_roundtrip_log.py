"""Tests of the Dream-1 log: one line per request, from the dream client's on_round_trip() hook."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MUJOCO_GL", "egl")

import numpy as np  # noqa: E402

import dream_control  # noqa: E402
import roundtrip_log  # noqa: E402
from yam_env import YamEnv  # noqa: E402

TIMING = {"rtt_ms": 512.3, "serve_infer_ms": 412, "gateway_ms": 460, "network_ms": 52.3, "up_mbps": 38.2}


def round_trip(action=None, error=None):
    return SimpleNamespace(
        sent_s=time.time(),
        frames={"image_window.top": ["a"] * 9, "image_window.left": ["b"] * 9},
        observation={"prompt": "put apple into the bowl", "state.arm_l.joint_pos": np.zeros(6)},
        action=action,
        timing=None if error else TIMING,
        error=error,
    )


class Refusal(Exception):
    code = "DA-2000"
    reason = "rate_limited"
    retryable = True


class FakeClient:
    """Answers get_action() like Dream-1 and calls the on_round_trip() functions as the dream client does."""

    def __init__(self, env):
        self.env = env
        self.fns = []

    def on_round_trip(self, fn):
        self.fns.append(fn)

    def set_observation(self, frames, state, timestamp=0.0):
        pass

    def get_action(self):
        s = self.env.state()
        action = {"q_l_chunk": np.tile(s["left"], (48, 1)).tolist(), "q_r_chunk": np.tile(s["right"], (48, 1)).tolist()}
        for fn in self.fns:
            fn(round_trip(action))
        return action

    def close(self):
        pass


class EntryTest(unittest.TestCase):
    def test_reply(self):
        line = roundtrip_log.entry(round_trip({"q_l_chunk": [[0.123456] * 7] * 48, "q_r_chunk": [[0] * 7] * 40}), 3, 12.3456)
        self.assertEqual(line["n"], 3)
        self.assertEqual(line["sim_s"], 12.346)
        self.assertEqual(line["prompt"], "put apple into the bowl")
        self.assertEqual(line["state"], {"state.arm_l.joint_pos": [0.0] * 6})
        self.assertEqual(line["frames"], {"image_window.top": 9, "image_window.left": 9})
        self.assertEqual(line["rows"], 48)
        self.assertEqual(line["chunk"]["q_l_chunk"][0][0], 0.1235)
        self.assertEqual(line["timing"], {k: TIMING[k] for k in ("rtt_ms", "serve_infer_ms", "gateway_ms", "network_ms")})
        self.assertIsNone(line["error"])
        json.dumps(line)  # every value is JSON

    def test_refusal(self):
        line = roundtrip_log.entry(round_trip(error=Refusal("no free seat")), 1)
        self.assertIsNone(line["rows"])
        self.assertIsNone(line["chunk"])
        self.assertEqual(line["timing"], {})
        self.assertEqual(line["error"]["code"], "DA-2000")
        self.assertEqual(line["error"]["reason"], "rate_limited")
        self.assertTrue(line["error"]["retryable"])


class LogFileTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_read_and_summary(self):
        path = self.folder / "run" / "attempt-1.dream.jsonl"  # the folder does not exist yet
        log = roundtrip_log.RoundTripLog(path)
        log(round_trip({"q_l_chunk": [[0] * 7] * 48, "q_r_chunk": [[0] * 7] * 48}))
        log(round_trip(error=Refusal("no free seat")))
        log.close()
        with open(path, "a") as stream:
            stream.write('{"n": 3, "cut sho')  # a line still being written
        compact = roundtrip_log.read(path)
        self.assertEqual([e["n"] for e in compact["entries"]], [1, 2])
        self.assertNotIn("chunk", compact["entries"][0])
        self.assertIn("chunk", roundtrip_log.read(path, full=True)["entries"][0])
        summary = compact["summary"]
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["error_codes"], ["DA-2000"])
        self.assertEqual(summary["rows"], 48)
        self.assertEqual(summary["serve_infer_ms"], {"mean": 412, "median": 412, "max": 412})

    def test_missing_file_reads_empty(self):
        self.assertEqual(roundtrip_log.read(self.folder / "none.jsonl")["entries"], [])

    def test_unwritable_path_never_raises(self):
        blocker = self.folder / "file"
        blocker.write_text("")
        log = roundtrip_log.RoundTripLog(blocker / "attempt-1.dream.jsonl")  # a file where a folder must be
        log(round_trip({"q_l_chunk": [], "q_r_chunk": []}))
        log.close()


class DreamControlTest(unittest.TestCase):
    def test_every_request_is_logged(self):
        path = Path(tempfile.mkdtemp()) / "attempt-1.dream.jsonl"
        env = YamEnv()
        clients = []

        def open_policy(config, key):
            clients.append(FakeClient(env))
            return clients[-1]

        saved = dream_control.open_policy, dream_control.settings
        dream_control.open_policy = open_policy
        dream_control.settings = lambda: {"DREAM_API_KEY": "fake-not-a-key"}
        try:
            dream_control.main(
                ["--headless", "--quiet", "--seconds", "3", "--round-trip-log", str(path)], shared_env=env
            )
        finally:
            dream_control.open_policy, dream_control.settings = saved
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertGreaterEqual(len(lines), 1)
        self.assertEqual([line["n"] for line in lines], list(range(1, len(lines) + 1)))
        self.assertTrue(all(line["rows"] == 48 for line in lines))
        self.assertEqual(lines[0]["prompt"], "put apple into the bowl")
        self.assertGreater(lines[-1]["sim_s"], 0)


if __name__ == "__main__":
    unittest.main()
