"""SortFlow's success checker for the Dream YAM sim.

Dream-1 never says whether a task worked, so the sim runs until its timer ends. This module reads the object
positions from MuJoCo and tells, for a task like "put apple into the bowl", whether the item rests in the target.
viewer_workspace_checked.py (started by yam_mujoco_checked.py) is the one caller: it stops the run on success.
"""

import re
import threading
import time

import mujoco
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


# The website backend's rule (dreamlabs_integration_test/backend/main.py, SimAdapter.goal_reached), for any body: the
# item's centre within GOAL_RADIUS_M of the target's centre, nearly still, touching the target and no gripper, for
# GOAL_SETTLED_FRAMES rendered frames in a row (30 control steps, 1.5 simulated seconds). Hidden attempts use it so
# they judge success as the live one does.
GOAL_RADIUS_M = {"bowl": 0.08, "plate": 0.07}
GOAL_SPEED_M_S = 0.03
GOAL_SETTLED_FRAMES = 30


def contact_goal_reached(env, name, target):
    """Whether a body currently meets the website's goal rule for a target.
    @param env Simulation environment.
    @param name The item's body name, such as "apple" or "stl_cup".
    @param target `bowl` or `plate`.
    @return True when the item is within the target's radius, nearly still, touching the target and no gripper.
    """
    model, data = env.model, env.data
    body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if body < 0 or model.body_dofnum[body] != 6 or model.body_jntnum[body] != 1:
        return False
    offset = np.linalg.norm(data.body(name).xpos[:2] - data.body(target).xpos[:2])
    dof = model.body_dofadr[body]
    if offset >= GOAL_RADIUS_M[target] or np.linalg.norm(data.qvel[dof : dof + 3]) >= GOAL_SPEED_M_S:
        return False
    touches_target = False
    for contact in data.contact[: data.ncon]:
        names = {model.body(model.geom_bodyid[contact.geom1]).name, model.body(model.geom_bodyid[contact.geom2]).name}
        if name not in names:
            continue
        other = next(iter(names - {name}), "")
        if other.startswith(("left_", "right_")):
            return False
        touches_target = touches_target or other == target
    return touches_target


class GoalWatcher:
    """Like SuccessWatcher, but with the website's contact rule, for any body in the scene.

    It checks inside the simulation's own render step (dream_control.capture calls env.images() after every control
    step), as the website backend does, so it never reads the scene while another thread is stepping it.
    """

    def __init__(self, env, name, target, done):
        """Create the watcher; call start() to begin and join() to finish.
        @param env Simulation environment.
        @param name The item's body name.
        @param target `bowl` or `plate`.
        @param done threading.Event set on success.
        """
        self.env, self.name, self.target, self.done = env, name, target, done
        self.stop = threading.Event()  # kept so callers can treat it like SuccessWatcher; checking ends at join()
        self.success_time = None  # simulated seconds from start(); None until it succeeds
        self.min_distance = float("inf")  # closest the item's centre came to the target's, metres
        self._frames = 0
        self._start_time = 0.0
        self._inner = None
        self._wrapped_before = False

    def start(self):
        """Begin checking after every rendered frame."""
        self._start_time = self.env.data.time
        self._wrapped_before = "images" in self.env.__dict__  # another wrapper (a recording) may already be there
        self._inner = self.env.images

        def images():
            result = self._inner()
            self._check()
            return result

        self.env.images = images

    def _check(self):
        if self.success_time is not None:
            return
        data = self.env.data
        self.min_distance = min(
            self.min_distance, float(np.linalg.norm(data.body(self.name).xpos[:2] - data.body(self.target).xpos[:2]))
        )
        self._frames = self._frames + 1 if contact_goal_reached(self.env, self.name, self.target) else 0
        if self._frames >= GOAL_SETTLED_FRAMES:
            self.success_time = data.time - self._start_time
            self.done.set()

    def join(self, timeout=None):
        """Stop checking and give the environment the images() it had before start()."""
        if self._inner is not None:
            if self._wrapped_before:
                self.env.images = self._inner
            else:
                self.env.__dict__.pop("images", None)
            self._inner = None
