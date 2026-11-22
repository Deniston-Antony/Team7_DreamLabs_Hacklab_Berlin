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


class Slow:
    def __init__(self, seconds):
        self.seconds = seconds

    def get_action(self):
        time.sleep(self.seconds)
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

    def test_clock_counts_decision_time(self):
        clock = capacity.AttemptClock(5, threading.Event())
        client = Slow(0.08)
        capacity.CapacityClient(client, clock).get_action()
        self.assertGreaterEqual(clock.decided(), 0.08)
        self.assertEqual(clock.moved(), 0)
        clock.tick()
        self.assertAlmostEqual(clock.used(), clock.moved() + clock.decided(), places=6)
        self.assertEqual(clock.moved(), capacity.CONTROL_STEP_S)
        clock.close()

    def test_waiting_for_a_seat_is_counted(self):
        clock = capacity.AttemptClock(5, threading.Event())
        client = Client([Refused("max_serves")])
        capacity.CapacityClient(client, clock).get_action()
        self.assertGreaterEqual(clock.waited(), 0.9)
        self.assertLess(clock.decided(), 0.2)
        self.assertAlmostEqual(clock.used(), clock.waited() + clock.decided(), places=6)
        clock.close()

    def test_limit_can_be_reached_while_waiting_for_a_seat(self):
        stop = threading.Event()
        clock = capacity.AttemptClock(0.1, stop)
        with self.assertRaises(capacity.TimeLimitReached):
            capacity.CapacityClient(Client([Refused("max_serves")]), clock).get_action()
        self.assertTrue(stop.is_set())
        self.assertEqual(clock.used(), 0.1)
        self.assertLessEqual(clock.waited(), 0.1)
        clock.close()

    def test_limit_can_be_reached_during_a_decision(self):
        stop = threading.Event()
        limits = []
        clock = capacity.AttemptClock(0.05, stop, lambda: limits.append(True))
        capacity.CapacityClient(Slow(0.2), clock).get_action()
        self.assertTrue(stop.is_set())
        self.assertTrue(limits)
        self.assertEqual(clock.used(), 0.05)
        self.assertEqual(clock.decided(), 0.05)
        clock.close()


if __name__ == "__main__":
    unittest.main()
