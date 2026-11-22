"""The dream client's own diagnostic log (`~/.dream/logs/dream.log`), read back for the website.

The dream package writes one line per session boundary (started, replaced, released), every switch of its pacing
between sync and async with the round trip that caused it, and any warning. It holds no robot data, prompts or api
key (see dream/logs.py). Every process on this computer that uses the client writes the same file: the live run and
every parallel worker, so a task's lines are the ones between its start and its end.

Line format (dream/logs.py): `2026-11-22T09:36:20.829Z INFO dream.pacing <message>`, in UTC.
"""

import calendar
import os
import re
import time
from pathlib import Path

LINE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d{3})Z (\w+) (\S+) (.*)$")
TAIL_BYTES = 2 * 1024 * 1024  # how far back from the end of the file to read
ROUND_TRIP = re.compile(r"median round trip is (\d+) ms \(last (\d+) ms\)")
SESSION = re.compile(r"session (\S+) (started|released|replaced)(?: \((\w+)\))?")


def path():
    """Where the dream client writes its log: `DREAM_LOG_DIR` if set, else `~/.dream/logs` (as dream/logs.py)."""
    override = os.environ.get("DREAM_LOG_DIR")
    return (Path(override).expanduser() if override else Path.home() / ".dream" / "logs") / "dream.log"


def parse(line):
    """One log line as a dictionary, or None for a line that is not one (a wrapped traceback, say)."""
    match = LINE.match(line.rstrip("\n"))
    if not match:
        return None
    stamp, millis, level, logger, message = match.groups()
    entry = {
        "time_s": calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S")) + int(millis) / 1000,
        "level": level,
        "logger": logger,
        "message": message,
        "kind": "other",
    }
    trip = ROUND_TRIP.search(message)
    if logger.endswith("pacing") and trip:
        entry.update(kind="pacing", mode="sync" if "pacing sync" in message else "async",
                     median_ms=int(trip.group(1)), last_ms=int(trip.group(2)))
    session = SESSION.search(message)
    if session:
        entry.update(kind="session", session=session.group(1), event=session.group(2), reason=session.group(3))
    if level in ("WARNING", "ERROR", "CRITICAL"):
        entry["kind"] = "warning"
    return entry


def read(since=None, until=None, limit=300, file=None):
    """The newest lines between two times.
    @param since Seconds since the epoch; earlier lines are left out. None keeps them.
    @param until Seconds since the epoch; later lines are left out. None keeps them.
    @param limit The most lines returned (the newest).
    @param file The log file; None reads the dream client's.
    @return {"path", "exists", "entries": [...] oldest first, "summary"}.
    """
    log = Path(file) if file else path()
    entries = []
    try:
        with open(log, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - TAIL_BYTES))
            text = stream.read().decode("utf-8", errors="replace")
        lines = text.splitlines()
        if size > TAIL_BYTES:
            lines = lines[1:]  # the first may start mid-line
        for line in lines:
            entry = parse(line)
            if entry is None:
                continue
            if since is not None and entry["time_s"] < since:
                continue
            if until is not None and entry["time_s"] > until:
                continue
            entries.append(entry)
        exists = True
    except FileNotFoundError:
        exists = False
    entries = entries[-limit:]
    sessions = {e["session"] for e in entries if e["kind"] == "session" and e["event"] == "started"}
    return {
        "path": str(log),
        "exists": exists,
        "entries": entries,
        "summary": {
            "sessions": len(sessions),
            "replaced": sum(1 for e in entries if e["kind"] == "session" and e["event"] == "replaced"),
            "warnings": sum(1 for e in entries if e["kind"] == "warning"),
            "sync_switches": sum(1 for e in entries if e.get("mode") == "sync"),
        },
    }
