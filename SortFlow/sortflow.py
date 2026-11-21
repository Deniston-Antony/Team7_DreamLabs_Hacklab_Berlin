"""SortFlow's success checker for the Dream YAM sim.

Dream-1 never says whether a task worked, so the sim runs until its timer ends. This module reads the object
positions from MuJoCo and tells, for a task like "put apple into the bowl", whether the item rests in the target.
viewer_workspace_checked.py (started by yam_mujoco_checked.py) is the one caller: it stops the run on success.
"""

import re
import threading
import time

import numpy as np

# Where an item must end up. Geometry comes from yam_env.py: bowl centre (0, -.04) with a 76 mm inner radius at
# the rim, plate centre (-.22, -.045) with a 69 mm radius. `radius` is how far the item's centre may sit from the
# target centre, `zmax` how high (an item held in mid-air above the bowl is not placed).
TARGETS = {
    "bowl": {"xy": (0.0, -0.04), "radius": 0.060, "zmin": 0.765, "zmax": 0.835},
    "plate": {"xy": (-0.22, -0.045), "radius": 0.060, "zmin": 0.760, "zmax": 0.835},
}
# The sim's body names, by the short name users type.
BODIES = {"apple": "apple", "orange": "orange", "banana": "banana", "bottle": "water_bottle"}
# Words a task may use for each short name.
ALIASES = {"water bottle": "bottle", "bottle": "bottle", "apple": "apple", "orange": "orange", "banana": "banana"}
HOLD_S = 1.0  # simulated seconds the item must rest in the target
STILL_M = 0.01  # how far it may drift during that time, metres


def parse_task(text):
    """Find the item and target a task names, such as "put apple into the bowl".
    @param text The task as typed.
    @return (item, target), or None when the task names no item and target this module can check.
    """
    low = text.lower()
    item = next((ALIASES[k] for k in sorted(ALIASES, key=len, reverse=True) if re.search(rf"\b{k}\b", low)), None)
    target = next((t for t in TARGETS if re.search(rf"\b{t}\b", low)), None)
    return (item, target) if item and target else None


def is_inside(env, item, target):
    """Whether an item's centre is inside a target's footprint, ignoring how long it has been there.
    @param env Simulation environment.
    @param item Short item name, a key of BODIES.
    @param target Target name, a key of TARGETS.
    @return True when the item is within the target's radius and height band.
    """
    spec = TARGETS[target]
    x, y, z = env.data.body(BODIES[item]).xpos
    close = np.hypot(x - spec["xy"][0], y - spec["xy"][1]) <= spec["radius"]
    return bool(close and spec["zmin"] <= z <= spec["zmax"])


class SuccessWatcher(threading.Thread):
    """Sets `done` once the item has rested inside its target for HOLD_S simulated seconds."""

    def __init__(self, env, item, target, done):
        """Create the watcher; call start() to begin.
        @param env Simulation environment, stepped by another thread.
        @param item Short item name.
        @param target Target name.
        @param done threading.Event set on success.
        """
        super().__init__(daemon=True)
        self.env, self.item, self.target, self.done = env, item, target, done
        self.stop = threading.Event()
        self.success_time = None  # simulated seconds from the start; None until it succeeds
        self.min_distance = float("inf")  # closest the item's centre came to the target's, metres, ignoring height
        self._start_time = env.data.time
        self._since = None
        self._anchor = None

    def run(self):
        """Poll the scene ten times a second until success or stop()."""
        body = self.env.data.body(BODIES[self.item])
        centre = np.array(TARGETS[self.target]["xy"])
        while not self.stop.is_set():
            now = self.env.data.time
            self.min_distance = min(self.min_distance, float(np.linalg.norm(body.xpos[:2] - centre)))
            if is_inside(self.env, self.item, self.target):
                pos = body.xpos.copy()
                if self._since is None or np.linalg.norm(pos - self._anchor) > STILL_M:
                    self._since, self._anchor = now, pos
                elif now - self._since >= HOLD_S:
                    self.success_time = now - self._start_time
                    self.done.set()
                    return
            else:
                self._since = None
            time.sleep(0.1)
