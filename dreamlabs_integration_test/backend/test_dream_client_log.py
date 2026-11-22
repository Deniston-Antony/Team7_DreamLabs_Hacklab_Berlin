"""Tests of reading the dream client's own log back for the website."""

import os
import tempfile
import time
import unittest
from pathlib import Path

import dream_client_log


def stamp(seconds):
    """A time as the dream client writes it: UTC, milliseconds, Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{int(seconds * 1000) % 1000:03d}Z"


class ParseTest(unittest.TestCase):
    def test_pacing_line(self):
        entry = dream_client_log.parse(
            "2026-11-22T09:36:20.829Z INFO dream.pacing pacing sync: get_action's median round trip is 2328 ms "
            "(last 2328 ms), over the 700 ms threshold"
        )
        self.assertEqual((entry["kind"], entry["mode"], entry["median_ms"], entry["last_ms"]), ("pacing", "sync", 2328, 2328))
        self.assertAlmostEqual(entry["time_s"], 1795340180.829, places=3)

    def test_session_lines(self):
        started = dream_client_log.parse("2026-11-22T09:36:21.131Z INFO dream.client session 143ea57c started (new)")
        released = dream_client_log.parse("2026-11-22T09:38:19.630Z INFO dream.client session 143ea57c released")
        self.assertEqual((started["kind"], started["event"], started["reason"]), ("session", "started", "new"))
        self.assertEqual((released["event"], released["reason"]), ("released", None))

    def test_warning_and_junk(self):
        self.assertEqual(dream_client_log.parse("2026-11-22T09:36:21.131Z WARNING dream.client slow")["kind"], "warning")
        self.assertIsNone(dream_client_log.parse("Traceback (most recent call last):"))


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.file = Path(tempfile.mkdtemp()) / "dream.log"
        now = time.time()
        self.file.write_text(
            "\n".join(
                [
                    f"{stamp(now - 100)} INFO dream.client session old started (new)",
                    f"{stamp(now - 10)} INFO dream.client session a started (new)",
                    "  a wrapped line",
                    f"{stamp(now - 9)} INFO dream.pacing pacing sync: get_action's median round trip is 900 ms (last 900 ms), over",
                    f"{stamp(now - 8)} WARNING dream.client round trips stay slow",
                    f"{stamp(now - 7)} INFO dream.client session a replaced (expired)",
                ]
            )
            + "\n"
        )
        self.now = now

    def test_window_and_summary(self):
        log = dream_client_log.read(self.now - 20, None, file=self.file)
        self.assertTrue(log["exists"])
        self.assertEqual(len(log["entries"]), 4)
        self.assertEqual(log["summary"], {"sessions": 1, "replaced": 1, "warnings": 1, "sync_switches": 1})
        self.assertEqual(len(dream_client_log.read(None, self.now - 50, file=self.file)["entries"]), 1)
        self.assertEqual(len(dream_client_log.read(file=self.file, limit=2)["entries"]), 2)

    def test_missing_file(self):
        log = dream_client_log.read(file=self.file.parent / "none.log")
        self.assertFalse(log["exists"])
        self.assertEqual(log["entries"], [])

    def test_default_path_follows_dream_log_dir(self):
        saved = os.environ.get("DREAM_LOG_DIR")
        os.environ["DREAM_LOG_DIR"] = str(self.file.parent)
        try:
            self.assertEqual(dream_client_log.path(), self.file)
            self.assertEqual(len(dream_client_log.read()["entries"]), 5)
        finally:
            if saved is None:
                del os.environ["DREAM_LOG_DIR"]
            else:
                os.environ["DREAM_LOG_DIR"] = saved


if __name__ == "__main__":
    unittest.main()
