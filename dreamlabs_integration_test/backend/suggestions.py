"""Build one-change test scenes from the saved starting scenes of a finished run."""

import math
from pathlib import Path

import mujoco
import numpy as np

import environments
from judges import task_prompt
from evaluation.refinement import candidates, test_slots

STEPS_M = (0.10, 0.08, 0.06, 0.04)
MAX_SECONDS = 200
SAME_SCENE_M = 0.03
MIN_NEARER_M = 0.03
TURNS_DEG = (0, 25, -25, 50, -50)
CLEAR_GAP_M = 0.15
CLEARER_NAMES = {"apple": "red apple", "orange": "orange fruit", "banana": "yellow banana", "water_bottle": "water bottle"}


def scene_files(folder: Path, scene: int):
    """The saved model and starting state of a scene in a run's folder, or None for an older run without them."""
    if scene >= 2 and (folder / f"env-{scene}.mjb").is_file() and (folder / f"env-{scene}.npz").is_file():
        return folder / f"env-{scene}.mjb", folder / f"env-{scene}.npz"
    if (folder / "scene.mjb").is_file() and (folder / "scene.npz").is_file():
        return folder / "scene.mjb", folder / "scene.npz"
    return None


class TestScene:
    def __init__(self, model, state, snapshot, rules):
        self.model, self.snapshot, self.rules = model, snapshot, rules
        self.data = mujoco.MjData(model)
        self.data.qpos[:], self.data.qvel[:], self.data.ctrl[:] = state["qpos"], state["qvel"], state["ctrl"]
        mujoco.mj_forward(model, self.data)

    def xy(self, name):
        return self.data.body(name).xpos[:2].copy()

    def matches_saved_start(self, name):
        item = self.snapshot.get(name)
        return item is not None and item.position is not None and np.linalg.norm(self.xy(name) - item.position[:2]) <= SAME_SCENE_M

    def fits(self, name, point):
        x_range, y_range = self.rules["x"], self.rules["y"]
        if not (x_range[0] <= point[0] <= x_range[1] and y_range[0] <= point[1] <= y_range[1]):
            return False
        for item in self.snapshot.objects:
            if item.object_id == name:
                continue
            needed = self.rules["spacing"] if item.movable else self.rules["clearance"].get(item.object_id, 0.09)
            if np.linalg.norm(point - self.xy(item.object_id)) < needed:
                return False
        return True

    def place(self, name, point):
        joint = environments._free_joint(self.model, name)
        if joint is None or not self.fits(name, point):
            return False
        saved = self.data.qpos[joint[0] : joint[0] + 2].copy()
        self.data.qpos[joint[0] : joint[0] + 2] = point
        self.data.qvel[joint[1] : joint[1] + 6] = 0
        if environments._clear(self.model, self.data, {name}):
            return True
        self.data.qpos[joint[0] : joint[0] + 2] = saved
        mujoco.mj_forward(self.model, self.data)
        return False

    def move_toward(self, name, goal, stop_short=0.05):
        """Move an object nearer a point, trying a straight line first and then nearby free directions.
        @return How many metres nearer it ended, or None when no free place is at least 3 cm nearer.
        """
        start = self.xy(name)
        offset = goal - start
        distance = float(np.linalg.norm(offset))
        if distance <= stop_short:
            return None
        heading = math.atan2(offset[1], offset[0])
        for step in STEPS_M:
            if step >= distance - stop_short:
                continue
            for turn in TURNS_DEG:
                angle = heading + math.radians(turn)
                point = start + step * np.array([math.cos(angle), math.sin(angle)])
                nearer = distance - float(np.linalg.norm(goal - point))
                if nearer >= MIN_NEARER_M and self.place(name, point):
                    return nearer
        return None

    def nearest_arm(self, name):
        arms = []
        for side in ("left", "right"):
            site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, f"{side}_tcp")
            if site >= 0:
                arms.append((float(np.linalg.norm(self.data.site_xpos[site][:2] - self.xy(name))), side, self.data.site_xpos[site][:2].copy()))
        return min(arms, key=lambda item: item[0]) if arms else None

    def clear_around(self, name):
        others = [item for item in self.snapshot.objects if item.object_id != name]
        if not others:
            return None
        center = self.xy(name)
        nearest = min(others, key=lambda item: np.linalg.norm(self.xy(item.object_id) - center))
        start = self.xy(nearest.object_id)
        gap = float(np.linalg.norm(start - center))
        if not nearest.movable or gap >= CLEAR_GAP_M or gap <= 0:
            return None
        direction = (start - center) / gap
        for step in STEPS_M:
            if self.place(nearest.object_id, start + direction * step):
                return nearest.object_id, step
        return None

    def turn_like(self, name, reference):
        joint = environments._free_joint(self.model, name)
        if joint is None or reference is None or reference.orientation_wxyz is None:
            return False
        saved = self.data.qpos[joint[0] + 3 : joint[0] + 7].copy()
        wanted = np.array(reference.orientation_wxyz, dtype=float)
        wanted /= np.linalg.norm(wanted)
        if abs(float(np.dot(saved, wanted))) > 0.985:
            return False
        self.data.qpos[joint[0] + 3 : joint[0] + 7] = wanted
        self.data.qvel[joint[1] : joint[1] + 6] = 0
        if environments._clear(self.model, self.data, {name}):
            return True
        self.data.qpos[joint[0] + 3 : joint[0] + 7] = saved
        mujoco.mj_forward(self.model, self.data)
        return False


def nice(name):
    return name.replace("_", " ")


def apply_change(scene, item, seconds, by_attempt):
    """Make the candidate's one change. @return (description, instruction, seconds), or None when it cannot be done."""
    target, destination = item["target"], item["destination"]
    kind = item["kind"]
    if kind == "move_target":
        arm = scene.nearest_arm(target)
        moved = scene.move_toward(target, arm[2]) if arm else None
        return None if moved is None else (f"Moved the {nice(target)} {moved * 100:.0f} cm closer to the {arm[1]} arm", item["instruction"], seconds)
    if kind == "shorter_carry":
        moved = scene.move_toward(target, scene.xy(destination))
        return None if moved is None else (f"Moved the {nice(target)} {moved * 100:.0f} cm closer to the {destination}", item["instruction"], seconds)
    if kind == "clear_space":
        cleared = scene.clear_around(target)
        return None if cleared is None else (f"Moved the {nice(cleared[0])} {cleared[1] * 100:.0f} cm away from the {nice(target)}", item["instruction"], seconds)
    if kind == "match_orientation":
        reference = by_attempt[item["reference_attempt"]][1].initial_snapshot.get(target)
        turned = scene.turn_like(target, reference)
        return None if not turned else (f"Turned the {nice(target)} to match attempt {item['reference_attempt']}", item["instruction"], seconds)
    if kind == "clearer_name":
        name = CLEARER_NAMES.get(target)
        if not name:
            return None
        word = "on" if destination == "plate" else "into"
        return f"Named the object '{name}'", f"put the {name} {word} the {destination}", seconds
    if kind == "more_time":
        longer = min(MAX_SECONDS, int(round(seconds * 1.5)))
        return None if longer <= seconds else (f"Raised the time limit from {seconds:g} s to {longer} s", item["instruction"], longer)
    return None


def build_tests(folder, records, rules, judge="offline"):
    """Plan a run's tests from its saved scenes.
    @param folder The run's video folder, holding its saved scenes.
    @param records The run's (task, evidence, result) records.
    @param rules {"x", "y", "spacing", "clearance"} for where objects may be placed.
    @param judge The video judge whose task prompt the tests use.
    @return {"tests", "untested", "problem"}; each test keeps its model path and state arrays.
    """
    by_attempt = {record[0].attempt: record for record in records}
    slots, control_count = test_slots(len(records))
    models = {}

    def open_scene(item):
        files = scene_files(Path(folder), item["source_scene"])
        evidence = by_attempt[item["source_attempt"]][1]
        if files is None or evidence.initial_snapshot is None:
            return None, None
        path = str(files[0])
        try:
            models.setdefault(path, mujoco.MjModel.from_binary_path(path))
            with np.load(files[1]) as saved:
                state = {name: saved[name].copy() for name in ("qpos", "qvel", "ctrl")}
            if models[path].nq != state["qpos"].size:
                return None, None
            scene = TestScene(models[path], state, evidence.initial_snapshot, rules)
            names = [entry.object_id for entry in evidence.initial_snapshot.objects]
            if any(mujoco.mj_name2id(models[path], mujoco.mjtObj.mjOBJ_BODY, name) < 0 for name in names):
                return None, None
        except (OSError, ValueError, KeyError):
            return None, None
        return scene, files[0]

    def prompt_for(item, clearer=False):
        written = by_attempt[item["source_attempt"]][0].instruction
        return task_prompt(written, {"object": item["target"], "target": item["destination"]}, judge, clearer)

    def record(item, scene, model_path, change, instruction, seconds, prompt):
        return {
            **{key: item[key] for key in ("kind", "label", "suggestion", "source_attempt", "source_scene", "target", "destination")},
            "instruction": instruction,
            "prompt": prompt,
            "change": change,
            "max_seconds": seconds,
            "objects": [entry.object_id for entry in scene.snapshot.objects],
            "model_path": str(model_path),
            "qpos": scene.data.qpos.copy(),
            "qvel": scene.data.qvel.copy(),
            "ctrl": scene.data.ctrl.copy(),
        }

    options = candidates(records)
    changes, used = [], set()
    missing_scene = False
    for item in options:
        if len(changes) >= slots - control_count:
            break
        if (item["source_attempt"], item["kind"]) in used:
            continue
        scene, model_path = open_scene(item)
        if scene is None:
            missing_scene = True
            continue
        if not scene.matches_saved_start(item["target"]):
            continue
        seconds = by_attempt[item["source_attempt"]][0].max_duration_seconds
        made = apply_change(scene, item, seconds, by_attempt)
        if made is None:
            continue
        prompt = prompt_for(item, item["kind"] == "clearer_name")
        if item["kind"] == "clearer_name" and prompt == prompt_for(item):
            continue  # this judge's prompt already names the object clearly, so there is nothing to change
        used.add((item["source_attempt"], item["kind"]))
        changes.append(record(item, scene, model_path, *made, prompt))
    if not changes:
        problem = "This older run did not save its starting scenes." if missing_scene else "None of this run's suggestions can be applied automatically."
        return {"tests": [], "untested": [], "problem": problem}
    controls = []
    sources = list(dict.fromkeys(item["source_attempt"] for item in changes))
    for attempt in sources[:control_count]:
        item = next(entry for entry in options if entry["source_attempt"] == attempt)
        scene, model_path = open_scene(item)
        seconds = by_attempt[attempt][0].max_duration_seconds
        control = {**item, "kind": "control", "label": "Unchanged control", "suggestion": "No change: this measures normal run-to-run variation."}
        controls.append(record(control, scene, model_path, "No change", by_attempt[attempt][0].instruction, seconds, prompt_for(item)))
    tests = controls + changes
    for number, item in enumerate(tests, 1):
        item["test"] = number
    tested = {item["source_attempt"] for item in tests}
    failed = [task.attempt for task, _, result in records if result.outcome != "SUCCESS"]
    return {"tests": tests, "untested": [attempt for attempt in failed if attempt not in tested], "problem": None}


def public(test):
    return {key: value for key, value in test.items() if key not in ("qpos", "qvel", "ctrl", "model_path", "objects")}
