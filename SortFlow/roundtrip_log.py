"""A run's Dream-1 log: one JSON line per request, from the dream client's own on_round_trip() hook.

The dream client hands every get_action() to on_round_trip() as a RoundTrip: what was sent (the state and the
prompt; the frames), what came back (the action chunk) or what was raised, and DreamClient.last_timing (the round
trip, the model's own inference time, the time inside Dream Labs' gateway and the network). This module writes one
line of it per request, so a run can be read back afterwards or while it runs. The frames are not written (27 JPEGs
per request); their count per camera is.

Each line:
- `n`: the request's number in this log, from 1.
- `sent_s`: when it went out, seconds since the epoch.
- `sim_s`: the simulator's time when the reply came back.
- `prompt`, `state`: what was sent besides the frames (`state` holds every non-prompt key).
- `frames`: frames sent per camera key.
- `rows`: rows in the returned chunk (the longer arm's), None when the call raised.
- `chunk`: the returned `q_l_chunk` and `q_r_chunk`, rounded to 4 decimals; None when it raised.
- `timing`: `rtt_ms` (the whole round trip), `serve_infer_ms` (the model's own time), `gateway_ms`, `network_ms`,
  `request_bytes`, `response_bytes`, `tcp_retrans`; any the client did not report are left out.
- `error`: None, or `type`, `code`, `reason`, `retryable`, `message` of what get_action() raised.
"""

import json
import sys
import threading
import time
from pathlib import Path

TIMING_KEYS = ("rtt_ms", "serve_infer_ms", "gateway_ms", "network_ms", "request_bytes", "response_bytes", "tcp_retrans")
CHUNK_KEYS = ("q_l_chunk", "q_r_chunk")


def _rounded(value):
    """Nested lists of floats rounded to 4 decimals; anything else as it is, when JSON can hold it."""
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    if hasattr(value, "tolist"):
        return _rounded(value.tolist())
    if isinstance(value, float):
        return round(value, 4)
    return value if isinstance(value, (int, str, bool)) or value is None else str(value)


def entry(round_trip, number, sim_s=None):
    """One RoundTrip as a log line's dictionary."""
    action = getattr(round_trip, "action", None)
    observation = dict(getattr(round_trip, "observation", None) or {})
    prompt = observation.pop("prompt", None)
    error = getattr(round_trip, "error", None)
    timing = getattr(round_trip, "timing", None) or {}
    chunk = None
    rows = None
    if isinstance(action, dict):
        chunk = {key: _rounded(action[key]) for key in CHUNK_KEYS if key in action}
        lengths = [len(v) for v in chunk.values() if isinstance(v, list)]
        rows = max(lengths) if lengths else None
    return {
        "n": number,
        "sent_s": getattr(round_trip, "sent_s", None),
        "sim_s": None if sim_s is None else round(float(sim_s), 3),
        "prompt": prompt,
        "state": {key: _rounded(value) for key, value in observation.items()},
        "frames": {key: len(value) for key, value in (getattr(round_trip, "frames", None) or {}).items()},
        "rows": rows,
        "chunk": chunk,
        "timing": {key: timing[key] for key in TIMING_KEYS if timing.get(key) is not None},
        "error": None
        if error is None
        else {
            "type": type(error).__name__,
            "code": getattr(error, "code", None),
            "reason": getattr(error, "reason", None),
            "retryable": getattr(error, "retryable", None),
            "message": str(error)[:500],
        },
    }


class RoundTripLog:
    """Appends one line per request to a file; register it with `client.on_round_trip(log)`.

    A write that fails is reported once on stderr and never stops the run.
    """

    def __init__(self, path, env=None):
        self.path = path
        self.env = env
        self.count = 0
        self._lock = threading.Lock()
        self._failed = False
        self._file = None
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "a", encoding="utf-8")
        except OSError as error:  # no log, but the run goes on
            self._report(error)

    def watch(self, client):
        """Log this client's requests; a client without the hook (an old dream package) is left alone."""
        if hasattr(client, "on_round_trip"):
            client.on_round_trip(self)

    def _report(self, error):
        if not self._failed:
            self._failed = True
            print(f"Dream-1 log could not be written: {error}", file=sys.stderr, flush=True)

    def __call__(self, round_trip):
        if self._file is None:
            return
        try:
            with self._lock:
                self.count += 1
                sim_s = getattr(getattr(self.env, "data", None), "time", None)
                self._file.write(json.dumps(entry(round_trip, self.count, sim_s)) + "\n")
                self._file.flush()
        except Exception as error:  # the log must never stop the robot
            self._report(error)

    def close(self):
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


def read(path, full=False):
    """A log's lines and their summary.
    @param path The log file.
    @param full Whether to keep each line's `state` and `chunk`; without them a line is small.
    @return {"entries": [...], "summary": summary(entries)}; no file reads as no entries.
    """
    entries = []
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue  # a line cut short while the run writes it
                if not full:
                    item.pop("state", None)
                    item.pop("chunk", None)
                entries.append(item)
    except FileNotFoundError:
        pass
    return {"entries": entries, "summary": summary(entries)}


def _stats(values):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return None
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {"mean": round(sum(values) / len(values), 1), "median": round(median, 1), "max": round(values[-1], 1)}


def summary(entries):
    """Requests, errors and the timing of a log's lines."""
    errors = [e for e in entries if e.get("error")]
    return {
        "requests": len(entries),
        "errors": len(errors),
        "error_codes": sorted({e["error"].get("code") or e["error"].get("type") for e in errors}),
        "rows": sum(e.get("rows") or 0 for e in entries),
        "rtt_ms": _stats(e.get("timing", {}).get("rtt_ms") for e in entries),
        "serve_infer_ms": _stats(e.get("timing", {}).get("serve_infer_ms") for e in entries),
        "network_ms": _stats(e.get("timing", {}).get("network_ms") for e in entries),
        "updated_s": time.time(),
    }
