"""Fair attempt clocks, and patient retries when Dream-1 has no free seat."""

import random
import threading
import time

CAPACITY_REASONS = {"max_serves", "pool_full", "queue_timeout", "pool_warming", "rate_limited"}
MAX_WAIT_S = 90.0
RUN_ALLOWANCE_S = MAX_WAIT_S + 30.0
WALL_TIME_FACTOR = 4.0
CONTROL_STEP_S = 0.05


def wall_limit(robot_seconds):
    """Real time a run may take to deliver its robot time, including Dream-1's thinking pauses."""
    return float(robot_seconds) * WALL_TIME_FACTOR + RUN_ALLOWANCE_S


class AttemptClock:
    """Counts executed robot motion, so thinking and waiting for a seat never use an attempt's time."""

    def __init__(self, limit_s, stop, on_limit=None):
        self.limit_s = float(limit_s)
        self.stop = stop
        self.on_limit = on_limit
        self.waiting = False
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._steps = 0

    def begin(self):
        with self._lock:
            self._started = True

    def pause(self):
        self.waiting = True

    def resume(self):
        self.waiting = False

    def tick(self):
        with self._lock:
            if not self._started or self._closed or self._steps * CONTROL_STEP_S >= self.limit_s:
                return
            self._steps += 1
            reached = self._steps * CONTROL_STEP_S >= self.limit_s - 1e-9
        if reached:
            if self.on_limit:
                self.on_limit()
            self.stop.set()

    def used(self):
        with self._lock:
            return min(self.limit_s, self._steps * CONTROL_STEP_S)

    def close(self):
        with self._lock:
            self._closed = True
        self.waiting = False


def is_capacity_refusal(error):
    return getattr(error, "reason", None) in CAPACITY_REASONS


class CapacityClient:
    """A Dream client whose get_action waits for a free seat while the simulated robot holds still."""

    def __init__(self, client, clock):
        self._client = client
        self._clock = clock

    def __getattr__(self, name):
        return getattr(self._client, name)

    def get_action(self, *args, **kwargs):
        waited, tries = 0.0, 0
        while True:
            try:
                reply = self._client.get_action(*args, **kwargs)
            except Exception as error:
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
                    raise
                continue
            self._clock.begin()
            return reply
