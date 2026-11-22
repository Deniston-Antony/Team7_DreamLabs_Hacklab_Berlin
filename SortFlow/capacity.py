"""Attempt clocks, and patient retries when Dream-1 has no free seat."""

import random
import threading
import time

CAPACITY_REASONS = {"max_serves", "pool_full", "queue_timeout", "pool_warming", "rate_limited"}
MAX_WAIT_S = 90.0
RUN_ALLOWANCE_S = MAX_WAIT_S + 30.0
WALL_TIME_FACTOR = 4.0
CONTROL_STEP_S = 0.05


def wall_limit(run_seconds):
    """The safety limit on real time for a run, which also covers loading."""
    return float(run_seconds) * WALL_TIME_FACTOR + RUN_ALLOWANCE_S


class TimeLimitReached(Exception):
    """The run's time ended while it was still waiting for a free Dream-1 seat."""


class AttemptClock:
    """Counts robot motion, Dream-1 decisions and waiting for a free seat. Loading is not counted."""

    def __init__(self, limit_s, stop, on_limit=None):
        self.limit_s = float(limit_s)
        self.stop = stop
        self.on_limit = on_limit
        self.waiting = False
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._steps = 0
        self._decided = 0.0
        self._waited = 0.0
        self._asked_at = None
        self._wait_from = None
        self._alarm = None

    def begin(self):
        with self._lock:
            self._started = True

    def _room(self):
        return max(0.0, self.limit_s - self._steps * CONTROL_STEP_S - self._decided - self._waited)

    def _pending(self, since):
        return 0.0 if since is None else min(time.monotonic() - since, self._room())

    def _total(self):
        return self._steps * CONTROL_STEP_S + self._decided + self._waited + self._pending(self._asked_at) + self._pending(self._wait_from)

    def _arm(self):
        self._disarm()
        self._alarm = threading.Timer(self._room() + 0.01, self._check)
        self._alarm.daemon = True
        self._alarm.start()

    def _disarm(self):
        if self._alarm:
            self._alarm.cancel()
            self._alarm = None

    def _check(self):
        with self._lock:
            reached = not self._closed and self._total() >= self.limit_s - 1e-9
        if reached:
            if self.on_limit:
                self.on_limit()
            self.stop.set()

    def tick(self):
        with self._lock:
            if not self._started or self._closed:
                return
            if self._total() < self.limit_s - 1e-9:
                self._steps += 1
        self._check()

    def ask(self):
        """A Dream-1 request starts; the limit can be reached while it is pending."""
        with self._lock:
            if self._closed:
                return
            self._asked_at = time.monotonic()
            self._arm()

    def answered(self, decided=True):
        """The pending request ended: with a decision, or refused, which counts as waiting."""
        with self._lock:
            amount = self._pending(self._asked_at)
            self._asked_at = None
            self._disarm()
            if decided:
                self._decided += amount
            else:
                self._waited += amount
        self._check()

    def pause(self):
        with self._lock:
            self.waiting = True
            if not self._closed:
                self._wait_from = time.monotonic()
                self._arm()

    def resume(self):
        with self._lock:
            self._waited += self._pending(self._wait_from)
            self._wait_from = None
            self._disarm()
            self.waiting = False
        self._check()

    def reached(self):
        with self._lock:
            return self._total() >= self.limit_s - 1e-9

    def used(self):
        with self._lock:
            return min(self.limit_s, self._total())

    def moved(self):
        with self._lock:
            return min(self.limit_s, self._steps * CONTROL_STEP_S)

    def decided(self):
        with self._lock:
            return self._decided + self._pending(self._asked_at)

    def waited(self):
        with self._lock:
            return self._waited + self._pending(self._wait_from)

    def close(self):
        with self._lock:
            self._decided += self._pending(self._asked_at)
            self._waited += self._pending(self._wait_from)
            self._asked_at = self._wait_from = None
            self._disarm()
            self._closed = True
        self.waiting = False


def is_capacity_refusal(error):
    return getattr(error, "reason", None) in CAPACITY_REASONS


class CapacityClient:
    """A Dream client whose get_action waits for a free seat while the robot holds still and the timer keeps running."""

    def __init__(self, client, clock):
        self._client = client
        self._clock = clock

    def __getattr__(self, name):
        return getattr(self._client, name)

    def get_action(self, *args, **kwargs):
        waited, tries = 0.0, 0
        while True:
            self._clock.ask()
            try:
                reply = self._client.get_action(*args, **kwargs)
            except Exception as error:
                self._clock.answered(decided=False)
                if not is_capacity_refusal(error) or waited >= MAX_WAIT_S:
                    raise
                backoff = min(8.0, 2.0**tries)
                delay = max(getattr(error, "retry_after_s", None) or 0.0, backoff) + random.uniform(0.0, 0.5)
                delay = min(delay, MAX_WAIT_S - waited)
                self._clock.pause()
                stopped = self._clock.stop.wait(delay)
                self._clock.resume()
                waited += delay
                tries += 1
                if stopped:
                    if self._clock.reached():
                        raise TimeLimitReached("The time limit passed while waiting for a free Dream-1 seat.") from error
                    raise
                continue
            self._clock.begin()
            self._clock.answered()
            return reply
