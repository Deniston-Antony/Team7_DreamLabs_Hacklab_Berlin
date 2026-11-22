"""Saved starting scenes: every run saves the exact scene of each of its runs, so a test can be repeated later.

A save is written when a task starts, before the robot moves. It holds the live scene (scene style, added and
removed objects, Movable/Fixed types and the full simulator state) and, for every parallel run, its scene style and
full simulator state. Loading it puts the live scene and every parallel run back exactly as they were: the full
state is restored, so nothing is re-placed or settled again.

On disk, one folder per save under SAVES (one per task id): `scene.json` with the description and `scene.npz` with
the arrays (simulator states and imported meshes). The newest MAX_SAVES are kept.
"""

import json
import os
import re
import shutil
import time
from pathlib import Path

import numpy as np

SAVES = Path(os.environ.get("DREAM_SCENE_SAVES", Path(__file__).resolve().parent / "scene_saves"))
MAX_SAVES = 100
SAVE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
VERSION = 1


class SaveError(Exception):
    """A save that cannot be read or loaded; `status` is the HTTP status to answer with."""

    def __init__(self, message, status=404):
        super().__init__(message)
        self.status = status


def write(save_id, task, live, environments, mode, seed):
    """Save one task's starting scenes.
    @param save_id The task id; also the save's folder name.
    @param task The task dictionary: `plan` (instructions and runs per task), max_seconds and attempts.
    @param live {"scene_id", "added", "removed", "type_overrides", "poses", "qpos", "qvel", "ctrl"} of the live
        scene; `poses` (name -> position and wxyz quaternion) places the fixed objects, which have no state.
    @param environments The parallel runs' environments.Environment, in run order (may be empty).
    @param mode The Start scenes mode the parallel runs came from.
    @param seed Their layout seed.
    @return The save's description, as list_saves gives it.
    """
    if not SAVE_ID.match(save_id):
        raise SaveError(f"invalid save id {save_id!r}", 422)
    folder = SAVES / save_id
    temporary = SAVES / f".{save_id}.tmp"
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    arrays = {"live_qpos": live["qpos"], "live_qvel": live["qvel"], "live_ctrl": live["ctrl"]}
    added = []
    for name, item in live["added"].items():
        mesh = item["vertices"] is not None
        if mesh:
            arrays[f"mesh_{name}_vertices"] = item["vertices"]
            arrays[f"mesh_{name}_faces"] = item["faces"]
        added.append(
            {
                "name": name,
                "position": [float(v) for v in item["position"]],
                "rgba": None if item["rgba"] is None else [float(v) for v in item["rgba"]],
                "mesh": mesh,
            }
        )
    runs = []
    for environment in environments:
        prefix = f"run{environment.attempt}"
        arrays[f"{prefix}_qpos"] = environment.qpos
        arrays[f"{prefix}_qvel"] = environment.qvel
        arrays[f"{prefix}_ctrl"] = environment.ctrl
        runs.append(
            {
                "attempt": environment.attempt,
                "style": environment.style_id,
                "shuffled": bool(environment.shuffled),
                "edited": bool(environment.edited),
                "seed": environment.seed,
                "label": environment.label,
            }
        )
    description = {
        "version": VERSION,
        "save_id": save_id,
        "saved_at": time.time(),
        "plan": [{"instruction": p["instruction"], "attempts": int(p.get("attempts", 1))} for p in task["plan"]],
        "total_runs": int(task["attempts"]),
        "max_seconds": int(task["max_seconds"]),
        "mode": mode,
        "seed": seed,
        "scene_id": live["scene_id"],
        "removed": sorted(live["removed"]),
        "type_overrides": live["type_overrides"],
        "added": added,
        "poses": {name: [float(v) for v in pose] for name, pose in live["poses"].items()},
        "runs": runs,
    }
    np.savez_compressed(temporary / "scene.npz", **arrays)
    (temporary / "scene.json").write_text(json.dumps(description))
    shutil.rmtree(folder, ignore_errors=True)
    temporary.rename(folder)
    _prune()
    return summary(description)


def summary(description):
    """The fields the Saved scenes list shows."""
    return {
        key: description[key]
        for key in ("save_id", "saved_at", "plan", "total_runs", "max_seconds", "mode", "seed", "scene_id")
    } | {"edited_runs": sum(1 for run in description["runs"] if run["edited"])}


def list_saves():
    """Every save, newest first."""
    saves = []
    for path in SAVES.glob("*/scene.json") if SAVES.is_dir() else []:
        try:
            saves.append(summary(json.loads(path.read_text())))
        except (OSError, ValueError, KeyError):
            continue  # a damaged save is left out, not fatal
    return sorted(saves, key=lambda item: item["saved_at"], reverse=True)


def read(save_id):
    """A save's description and arrays.
    @return (description, arrays) where arrays maps names to numpy arrays.
    """
    if not SAVE_ID.match(save_id):
        raise SaveError(f"invalid save id {save_id!r}", 422)
    folder = SAVES / save_id
    try:
        description = json.loads((folder / "scene.json").read_text())
        with np.load(folder / "scene.npz") as stored:
            arrays = {name: stored[name] for name in stored.files}
    except FileNotFoundError:
        raise SaveError(f"no saved scene {save_id!r}") from None
    except (OSError, ValueError) as error:
        raise SaveError(f"saved scene {save_id!r} cannot be read: {error}", 422) from error
    if description.get("version") != VERSION:
        raise SaveError(f"saved scene {save_id!r} is from another version", 422)
    return description, arrays


def added_objects(description, arrays):
    """The live scene's added objects in the form SimAdapter.added keeps them."""
    added = {}
    for item in description["added"]:
        mesh = item["mesh"]
        added[item["name"]] = {
            "position": np.array(item["position"]),
            "vertices": arrays[f"mesh_{item['name']}_vertices"] if mesh else None,
            "faces": arrays[f"mesh_{item['name']}_faces"] if mesh else None,
            "rgba": item["rgba"],
        }
    return added


def _prune():
    """Delete the oldest saves beyond MAX_SAVES."""
    folders = sorted(
        (p for p in SAVES.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in folders[MAX_SAVES:]:
        shutil.rmtree(old, ignore_errors=True)
