from dataclasses import dataclass, field

import numpy as np

from .config import ScoringConfig
from .models import ContactEvent, Evidence, Incident, ObjectState, TaskDefinition
from .verification import rotation_angle_deg, tilt_deg


@dataclass
class InteractionAnalysis:
    measured: bool
    contacts_measured: bool
    incidents: list[Incident] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    quality: float | None = None
    critical: bool = False
    target_contacted: bool | None = None
    wrong_objects: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def is_robot(name: str) -> bool:
    return name.endswith("_arm")


def off_table(state: ObjectState, evidence: Evidence, config: ScoringConfig) -> bool | None:
    environment = evidence.final_snapshot.environment
    if environment.table_bounds is None or environment.table_z is None or state.position is None:
        return None
    x_min, x_max, y_min, y_max = environment.table_bounds
    x, y, z = state.position
    return not (x_min <= x <= x_max and y_min <= y <= y_max) or z < environment.table_z - config.off_table_drop_m


def related(contacts: list[ContactEvent] | None, object_id: str) -> list[dict]:
    return [
        {"with": item.b if item.a == object_id else item.a, "start_s": item.start_s, "end_s": item.end_s}
        for item in contacts or []
        if object_id in (item.a, item.b)
    ]


def classify(start: ObjectState, end: ObjectState, protected: bool, evidence: Evidence, config: ScoringConfig):
    moved = float(np.linalg.norm(np.subtract(end.position, start.position)))
    turned = None
    tilt_change = None
    if start.orientation_wxyz and end.orientation_wxyz:
        turned = rotation_angle_deg(start.orientation_wxyz, end.orientation_wxyz)
        tilt_change = tilt_deg(end.orientation_wxyz) - tilt_deg(start.orientation_wxyz)
    if off_table(end, evidence, config):
        return "OBJECT_OFF_TABLE", "CRITICAL" if protected else "MAJOR", moved, turned, f"{start.object_id} was pushed off the table."
    if tilt_change is not None and tilt_change >= config.tipped_deg:
        return "OBJECT_TIPPED", "MAJOR", moved, turned, f"{start.object_id} tipped over by {tilt_change:.0f}°."
    if protected and (moved > config.protected_position_m or (turned or 0) > config.protected_rotation_deg):
        return "PROTECTED_MOVED", "MAJOR", moved, turned, f"Protected object {start.object_id} moved {moved * 100:.1f} cm and turned {turned or 0:.0f}°."
    if moved >= config.noticeable_displacement_m:
        return "NOTICEABLE_DISPLACEMENT", "MAJOR", moved, turned, f"{start.object_id} was moved {moved * 100:.1f} cm unintentionally."
    if moved >= config.minor_displacement_m:
        return "MINOR_DISPLACEMENT", "MINOR", moved, turned, f"{start.object_id} was moved {moved * 100:.1f} cm unintentionally."
    return None, None, moved, turned, ""


def analyze(task: TaskDefinition, evidence: Evidence, config: ScoringConfig) -> InteractionAnalysis:
    contacts = evidence.contacts
    analysis = InteractionAnalysis(
        measured=evidence.initial_snapshot is not None and evidence.final_snapshot is not None,
        contacts_measured=contacts is not None,
    )
    targets = {item.object_id for item in task.criteria}
    supports = {item.destination_id for item in task.criteria if item.destination_id}
    counts = {"intended": 0, "supporting": 0, "unintended_minor": 0, "unintended_major": 0, "critical": 0}
    severity_by_object: dict[str, str] = {}
    if analysis.measured:
        number = 1
        for start in sorted(evidence.initial_snapshot.objects, key=lambda item: item.object_id):
            end = evidence.final_snapshot.get(start.object_id)
            if start.object_id in targets:
                if end is not None and end.position is not None and off_table(end, evidence, config):
                    analysis.dropped.append(start.object_id)
                continue
            if end is None or start.position is None or end.position is None or not start.movable:
                continue
            protected = start.object_id in task.protected_objects
            kind, severity, moved, turned, text = classify(start, end, protected, evidence, config)
            events = related(contacts, start.object_id)
            robot_events = [event for event in events if is_robot(event["with"])]
            confidence = "simulator_ground_truth"
            if kind is None and protected and robot_events:
                kind, severity, confidence = "PROTECTED_COLLISION", "MINOR", "sampled_contact"
                text = f"The robot touched protected object {start.object_id}, but it stayed within tolerance."
            if kind is None:
                continue
            severity_by_object[start.object_id] = severity
            analysis.critical = analysis.critical or severity == "CRITICAL"
            if robot_events and kind != "PROTECTED_COLLISION":
                analysis.wrong_objects.append(start.object_id)
            analysis.incidents.append(
                Incident(
                    incident_id=f"incident_{number:03d}",
                    type=kind,
                    object_id=start.object_id,
                    severity=severity,
                    penalty=config.penalties[kind],
                    confidence=confidence,
                    evidence={
                        "initial_position": start.position,
                        "final_position": end.position,
                        "displacement_m": round(moved, 4),
                        "rotation_deg": None if turned is None else round(turned, 2),
                        "related_contacts": events[:10],
                    },
                    explanation=text,
                )
            )
            number += 1
    if contacts is not None:
        for event in contacts:
            pair = {event.a, event.b}
            objects = pair - {name for name in pair if is_robot(name)} - {"table"}
            if any(is_robot(name) for name in pair) and objects & targets:
                counts["intended"] += 1
            elif objects and objects <= targets | supports or (pair & targets and "table" in pair):
                counts["supporting"] += 1
            elif any(is_robot(name) for name in pair) and objects & supports:
                counts["supporting"] += 1
            else:
                worst = [severity_by_object.get(name) for name in objects]
                if "CRITICAL" in worst:
                    counts["critical"] += 1
                elif "MAJOR" in worst:
                    counts["unintended_major"] += 1
                else:
                    counts["unintended_minor"] += 1
        analysis.target_contacted = any(
            is_robot(event.a) and event.b in targets or is_robot(event.b) and event.a in targets for event in contacts
        )
        unnecessary = counts["unintended_minor"] + counts["unintended_major"] + counts["critical"]
        cost = unnecessary * config.unnecessary_contact_cost + len(analysis.dropped) * config.drop_cost
        analysis.quality = max(0.0, 1.0 - cost)
    analysis.counts = counts
    return analysis
