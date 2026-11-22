"""Offline checks for streaming camera frames over a pipe: no API call, no key."""

import io
import json
import os
import threading
import time
import unittest

import numpy as np
from PIL import Image

import livefeed
from yam_env import YamEnv


def collect(read_fd):
    """Every (kind, payload) the writer sent, until it closed its end."""
    with os.fdopen(read_fd, "rb") as stream:
        return list(livefeed.read_messages(stream))


class LivefeedTest(unittest.TestCase):
    """Frames arrive whole and in order, and a slow or missing reader never slows the run."""

    def setUp(self):
        """Open a fresh scene."""
        self.env = YamEnv()
        self.home = np.r_[np.zeros(6), 1.0]

    def tearDown(self):
        """Release the renderer."""
        self.env.close()

    def run_steps(self, count):
        """Step and render, as dream_control.capture does."""
        for _ in range(count):
            self.env.step(self.home, self.home)
            self.env.images()

    def test_frames_and_status_arrive_whole(self):
        """Every kind of message comes through: three cameras as valid JPEGs, and the final status."""
        read_fd, write_fd = os.pipe()
        messages = []
        reader = threading.Thread(target=lambda: messages.extend(collect(read_fd)))  # as the backend does
        reader.start()
        feed = livefeed.attach(self.env, os.fdopen(write_fd, "wb", buffering=0))
        self.run_steps(40)
        feed.status("succeeded", sim_seconds=2.0, closest_cm=0)
        feed.close()
        reader.join(timeout=5)
        frames = [(kind, payload) for kind, payload in messages if kind != livefeed.STATUS]
        self.assertEqual({livefeed.kind_name(kind) for kind, _ in frames}, {"top", "left", "right"})
        image = Image.open(io.BytesIO(frames[0][1]))
        self.assertEqual(image.size, (640, 360))
        self.assertLessEqual(len(frames), 3 * (40 // livefeed.EVERY + 1))
        last = json.loads([payload for kind, payload in messages if kind == livefeed.STATUS][-1])
        self.assertEqual((last["state"], last["closest_cm"]), ("succeeded", 0))
        self.assertNotIn("images", self.env.__dict__, "the environment's own images() is back")

    def test_a_reader_that_never_reads_does_not_slow_the_run(self):
        """With nobody reading, the pipe fills and the writer thread waits, but the simulation keeps running."""
        started = time.monotonic()
        self.run_steps(150)
        without_feed = time.monotonic() - started
        read_fd, write_fd = os.pipe()
        feed = livefeed.attach(self.env, os.fdopen(write_fd, "wb", buffering=0), every=1)
        started = time.monotonic()
        self.run_steps(150)
        took = time.monotonic() - started
        self.assertLess(took, without_feed * 1.5 + 1, "the simulation waited for the pipe")
        os.close(read_fd)  # the writer gets a broken pipe and ends
        feed.sender.close(timeout=0.5)

    def test_a_reader_that_goes_away_ends_the_writer_quietly(self):
        """Closing the read end makes the writer thread exit; the run is not interrupted."""
        read_fd, write_fd = os.pipe()
        feed = livefeed.attach(self.env, os.fdopen(write_fd, "wb", buffering=0), every=1)
        os.close(read_fd)
        self.run_steps(30)
        feed.sender.join(timeout=3)
        self.assertFalse(feed.sender.is_alive())
        feed.close()

    def test_only_the_newest_frame_is_kept_when_the_reader_is_slow(self):
        """A slow stream gets far fewer frames than were produced, and the last one produced is among them."""
        written = []

        class Slow:
            def write(self, data):
                time.sleep(0.03)
                written.append(data)

            def close(self):
                pass

        sender = livefeed.Sender(Slow())
        for value in range(40):
            sender.put(1, np.full((8, 8, 3), value * 6, dtype=np.uint8))
            time.sleep(0.005)
        sender.close(timeout=3)
        self.assertLess(len(written), 40)
        last = Image.open(io.BytesIO(written[-1][livefeed.HEADER.size :])).convert("RGB")
        self.assertAlmostEqual(np.asarray(last)[4, 4, 0], 39 * 6, delta=12)

    def test_a_cut_off_message_ends_reading_without_an_error(self):
        """If the writer dies mid-message, the reader stops at the last whole message."""
        whole = livefeed.pack(livefeed.STATUS, b'{"state": "running"}')
        stream = io.BytesIO(whole + livefeed.pack(1, b"x" * 100)[:50])
        self.assertEqual(list(livefeed.read_messages(stream)), [(livefeed.STATUS, b'{"state": "running"}')])


if __name__ == "__main__":
    unittest.main()
