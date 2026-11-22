import threading
import time
import unittest

import capacity


class Refused(Exception):
    def __init__(self, reason, retry_after_s=0.01):
        self.reason, self.retry_after_s, self.retryable = reason, retry_after_s, False


class Client:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    def get_action(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"ok": True}


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.backoff = capacity.random.uniform
        capacity.random.uniform = lambda *_: 0.0

    def tearDown(self):
        capacity.random.uniform = self.backoff

    def test_max_serves_is_retried(self):
        stop = threading.Event()
        clock = capacity.AttemptClock(5, stop)
        client = Client([Refused("max_serves")])
        self.assertEqual(capacity.CapacityClient(client, clock).get_action(), {"ok": True})
        self.assertEqual(client.calls, 2)
        clock.close()

    def test_other_errors_are_not_retried(self):
        clock = capacity.AttemptClock(5, threading.Event())
        client = Client([Refused("cannot_scale")])
        with self.assertRaises(Refused):
            capacity.CapacityClient(client, clock).get_action()
        self.assertEqual(client.calls, 1)
        clock.close()

    def test_clock_counts_only_robot_motion(self):
        stop = threading.Event()
        clock = capacity.AttemptClock(1, stop)
        clock.tick()
        self.assertEqual(clock.used(), 0)
        clock.begin()
        time.sleep(0.05)
        self.assertEqual(clock.used(), 0)
        for _ in range(19):
            clock.tick()
        self.assertFalse(stop.is_set())
        clock.tick()
        self.assertTrue(stop.is_set())
        self.assertEqual(clock.used(), 1)
        clock.close()


if __name__ == "__main__":
    unittest.main()
