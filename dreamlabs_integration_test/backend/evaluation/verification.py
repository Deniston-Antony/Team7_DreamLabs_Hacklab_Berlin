from collections.abc import Callable

import numpy as np

from .config import ScoringConfig
from .models import Check, Criterion, Evidence, ObjectState, Verification

Verifier = Callable[[Criterion, Evidence, ScoringConfig], Verification]
VERIFIERS: dict[str, Verifier] = {}
DIRECTIONS = {"left": (1.0, 0.0), "right": (-1.0, 0.0), "front": (0.0, 1.0), "back": (0.0, -1.0)}


def verifier(name: str):
    def register(function: Verifier) -> Verifier:
        VERIFIERS[name] = function
        return function

    return register


def not_measured(criterion: Criterion, detail: str) -> Verification:
    return Verification(
        criterion=criterion,
        status="NOT_MEASURED",
        fraction=None,
        checks=[Check(name="evidence", status="NOT_MEASURED", detail=detail)],
    )


def states(object_id: str | None, evidence: Evidence) -> tuple[ObjectState | None, ObjectState | None]:
    if evidence.initial_snapshot is None or evidence.final_snapshot is None:
        return None, None
    return evidence.initial_snapshot.get(object_id), evidence.final_snapshot.get(object_id)


def touching(evidence: Evidence, object_id: str, other: str) -> bool | None:
    if evidence.final_contacts is None:
        return None
    return any({object_id, other} == set(pair) for pair in evidence.final_contacts)


def held_by_robot(evidence: Evidence, object_id: str) -> bool | None:
    if evidence.final_contacts is None:
        return None
    return any(object_id in pair and any(name.endswith("_arm") for name in pair) for pair in evidence.final_contacts)


def lifted(evidence: Evidence, initial: ObjectState, config: ScoringConfig) -> bool | None:
    peak = evidence.max_heights.get(initial.object_id)
    if peak is None or initial.position is None:
        return None
    return peak - initial.position[2] >= config.lift_m


def finish(criterion: Criterion, checks: list[Check], done: bool, partial: float) -> Verification:
    if done:
        return Verification(criterion=criterion, status="PASS", fraction=1.0, checks=checks)
    if partial > 0:
        return Verification(criterion=criterion, status="PARTIAL", fraction=round(partial, 4), checks=checks)
    return Verification(criterion=criterion, status="FAIL", fraction=0.0, checks=checks)


def status(value: bool | None) -> str:
    return "NOT_MEASURED" if value is None else "PASS" if value else "FAIL"


def radius(object_id: str, state: ObjectState, config: ScoringConfig) -> float | None:
    if object_id in config.container_radii_m:
        return config.container_radii_m[object_id]
    if state.aabb_min is None or state.aabb_max is None:
        return None
    return float(max(state.aabb_max[0] - state.aabb_min[0], state.aabb_max[1] - state.aabb_min[1]) / 2)


@verifier("pick_place")
def pick_place(criterion: Criterion, evidence: Evidence, config: ScoringConfig) -> Verification:
    start, end = states(criterion.object_id, evidence)
    goal_start, goal_end = states(criterion.destination_id, evidence)
    if not all(item is not None and item.position is not None for item in (start, end, goal_start, goal_end)):
        return not_measured(criterion, "Initial or final pose of the object or destination is missing.")
    limit = radius(criterion.destination_id, goal_end, config)
    if limit is None:
        return not_measured(criterion, "The destination size is unavailable.")
    first = float(np.linalg.norm(np.subtract(start.position[:2], goal_start.position[:2])))
    last = float(np.linalg.norm(np.subtract(end.position[:2], goal_end.position[:2])))
    inside = last < limit and end.position[2] >= goal_end.position[2] - config.off_table_drop_m
    contact = touching(evidence, criterion.object_id, criterion.destination_id)
    held = held_by_robot(evidence, criterion.object_id)
    stable = None if end.speed_m_s is None else end.speed_m_s < config.stable_speed_m_s
    raised = lifted(evidence, start, config)
    progress = max(0.0, (first - last) / first) if first > 0 else 0.0
    checks = [
        Check(name="reached_destination", status=status(inside), detail=f"{last * 100:.1f} cm from the {criterion.destination_id} centre; limit {limit * 100:.1f} cm", value=round(last, 4)),
        Check(name="supported_by_destination", status=status(contact), detail=f"Final contact with {criterion.destination_id}: {contact}"),
        Check(name="released", status=status(None if held is None else not held), detail=f"Still touching the robot at the end: {held}"),
        Check(name="stable", status=status(stable), detail=f"Final speed: {end.speed_m_s if end.speed_m_s is not None else 'unavailable'} m/s"),
        Check(name="lifted", status=status(raised), detail=f"Peak height: {evidence.max_heights.get(criterion.object_id, 'unavailable')} m"),
    ]
    done = inside and contact is not False and held is not True and stable is not False
    partial = 0.2 + 0.5 * min(progress, 1.0) if raised or progress >= 0.25 or inside else 0.0
    return finish(criterion, checks, done, min(partial, 0.7))


@verifier("relocation")
def relocation(criterion: Criterion, evidence: Evidence, config: ScoringConfig) -> Verification:
    start, end = states(criterion.object_id, evidence)
    if start is None or end is None or start.position is None or end.position is None:
        return not_measured(criterion, "Initial or final pose of the object is missing.")
    axis = DIRECTIONS.get(criterion.direction or "")
    if axis is None:
        return not_measured(criterion, "The direction is not supported.")
    moved = float(np.dot(np.subtract(end.position[:2], start.position[:2]), axis))
    done = moved >= config.relocation_min_m
    checks = [
        Check(name="moved_in_direction", status="PASS" if done else "FAIL", detail=f"Moved {moved * 100:.1f} cm {criterion.direction} in the top-camera view; needs {config.relocation_min_m * 100:.1f} cm", value=round(moved, 4))
    ]
    fraction = moved / config.relocation_min_m
    return finish(criterion, checks, done, min(fraction, 0.7) if fraction >= 0.25 else 0.0)


@verifier("stacking")
def stacking(criterion: Criterion, evidence: Evidence, config: ScoringConfig) -> Verification:
    start, top = states(criterion.object_id, evidence)
    _, base = states(criterion.destination_id, evidence)
    if start is None or top is None or base is None or top.aabb_min is None or base.aabb_max is None or base.aabb_min is None:
        return not_measured(criterion, "Pose or object size is missing.")
    limit = radius(criterion.destination_id, base, config)
    offset = float(np.linalg.norm(np.subtract(top.position[:2], base.position[:2])))
    above = top.aabb_min[2] >= base.aabb_max[2] - config.stack_vertical_tolerance_m
    centred = offset < limit
    contact = touching(evidence, criterion.object_id, criterion.destination_id)
    held = held_by_robot(evidence, criterion.object_id)
    raised = lifted(evidence, start, config)
    checks = [
        Check(name="above_support", status=status(above), detail=f"Bottom {top.aabb_min[2]:.3f} m, support top {base.aabb_max[2]:.3f} m"),
        Check(name="centred", status=status(centred), detail=f"{offset * 100:.1f} cm from the support centre; limit {limit * 100:.1f} cm", value=round(offset, 4)),
        Check(name="supported", status=status(contact), detail=f"Final contact with {criterion.destination_id}: {contact}"),
        Check(name="released", status=status(None if held is None else not held), detail=f"Still touching the robot at the end: {held}"),
    ]
    done = above and centred and contact is not False and held is not True
    partial = 0.5 if above or centred else 0.25 if raised else 0.0
    return finish(criterion, checks, done, partial)


def quat_matrix(quat: list[float]) -> np.ndarray:
    w, x, y, z = np.asarray(quat, dtype=float) / np.linalg.norm(quat)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def rotation_angle_deg(first: list[float], second: list[float]) -> float:
    relative = quat_matrix(first).T @ quat_matrix(second)
    return float(np.degrees(np.arccos(np.clip((np.trace(relative) - 1) / 2, -1, 1))))


def tilt_deg(quat: list[float]) -> float:
    return float(np.degrees(np.arccos(np.clip(quat_matrix(quat)[2, 2], -1, 1))))


@verifier("rotation")
def rotation(criterion: Criterion, evidence: Evidence, config: ScoringConfig) -> Verification:
    start, end = states(criterion.object_id, evidence)
    if start is None or end is None or start.orientation_wxyz is None or end.orientation_wxyz is None:
        return not_measured(criterion, "Initial or final orientation is missing.")
    if criterion.upright:
        before, after = tilt_deg(start.orientation_wxyz), tilt_deg(end.orientation_wxyz)
        done = after <= config.rotation_tolerance_deg
        improvement = (before - after) / before if before > 0 else 0.0
        checks = [Check(name="upright", status="PASS" if done else "FAIL", detail=f"Tilt changed from {before:.1f}° to {after:.1f}°; limit {config.rotation_tolerance_deg:.0f}°", value=round(after, 2))]
        return finish(criterion, checks, done, min(max(improvement, 0.0), 0.7))
    wanted = abs(criterion.angle_deg or 0.0) % 360
    wanted = min(wanted, 360 - wanted)
    turned = rotation_angle_deg(start.orientation_wxyz, end.orientation_wxyz)
    error = abs(turned - wanted)
    done = error <= config.rotation_tolerance_deg
    checks = [Check(name="rotation_angle", status="PASS" if done else "FAIL", detail=f"Turned {turned:.1f}°; requested {wanted:.1f}° ± {config.rotation_tolerance_deg:.0f}°", value=round(turned, 2))]
    fraction = turned / wanted if wanted > 0 else 0.0
    return finish(criterion, checks, done, min(fraction, 0.7) if fraction >= 0.25 else 0.0)


def verify(criterion: Criterion, evidence: Evidence, config: ScoringConfig) -> Verification:
    function = VERIFIERS.get(criterion.type)
    if function is None:
        return not_measured(criterion, f"No verifier exists for task type {criterion.type!r}.")
    return function(criterion, evidence, config)
