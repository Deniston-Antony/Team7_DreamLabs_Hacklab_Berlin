import numpy as np

from .config import DEFAULT_CONFIG, ScoringConfig
from .interactions import InteractionAnalysis, analyze
from .models import Component, Evidence, TaskDefinition, TaskResult, Verification
from .verification import states, verify


def evaluate_task(task: TaskDefinition, evidence: Evidence, config: ScoringConfig = DEFAULT_CONFIG) -> TaskResult:
    if evidence.task_id != task.task_id:
        raise ValueError("The evidence belongs to a different task.")
    verification = [verify(item, evidence, config) for item in task.criteria]
    analysis = analyze(task, evidence, config)
    missing = missing_evidence(task, evidence)
    goal = goal_fraction(verification)
    fractions = component_fractions(task, evidence, config, verification, analysis, goal)
    outcome = decide_outcome(task, evidence, verification, analysis)
    components, base, total_penalty, coverage = score_components(fractions, analysis, config)
    score, caps = final_score(base, total_penalty, outcome, evidence, config)
    if outcome == "NOT_EVALUABLE":
        score = None
        caps = []
    understanding = scene_understanding(task, evidence, verification, analysis)
    good, bad, advice = explain(task, evidence, verification, analysis, outcome)
    return TaskResult(
        task_id=task.task_id,
        batch_id=task.batch_id,
        outcome=outcome,
        score=score,
        base_score=None if base is None else round(base, 2),
        total_penalties=round(total_penalty, 2),
        coverage=coverage,
        config_version=config.version,
        caps_applied=caps,
        components=components,
        verification=verification,
        scene_understanding=understanding,
        interactions=analysis.counts,
        incidents=analysis.incidents,
        missing_evidence=missing,
        summary=summary(task, outcome, score, analysis, evidence),
        went_well=good,
        went_wrong=bad,
        recommendations=advice,
    )


def missing_evidence(task: TaskDefinition, evidence: Evidence) -> list[str]:
    missing = list(task.unresolved)
    if evidence.initial_snapshot is None:
        missing.append("Initial scene snapshot")
    if evidence.final_snapshot is None:
        missing.append("Final scene snapshot")
    if evidence.contacts is None:
        missing.append("Contact events")
    if evidence.action_count is None and evidence.duration_seconds is None:
        missing.append("Action count and duration")
    if evidence.model_calls is None:
        missing.append("Dream-1 call count")
    return missing


def goal_fraction(verification: list[Verification]) -> float | None:
    values = [item.fraction for item in verification if item.fraction is not None]
    if not verification or len(values) != len(verification):
        return None
    return float(np.mean(values))


def object_correctness(task: TaskDefinition, evidence: Evidence, verification: list[Verification], analysis: InteractionAnalysis) -> tuple[float | None, str]:
    if not verification or any(item.status == "NOT_MEASURED" for item in verification):
        return None, "The required final poses are unavailable."
    earned, possible = 0.0, 0.0
    for item in verification:
        start, end = states(item.criterion.object_id, evidence)
        moved = bool(start and end and np.linalg.norm(np.subtract(start.position, end.position)) > 0.02)
        if analysis.target_contacted is not None:
            possible += 1
            earned += 1 if analysis.target_contacted or moved else 0
        placement = [check for check in item.checks if check.name in ("reached_destination", "moved_in_direction", "centred", "upright", "rotation_angle")]
        for check in placement:
            possible += 1
            earned += 1 if check.status == "PASS" else 0
    if possible == 0:
        return None, "No object-selection evidence is available."
    if analysis.wrong_objects:
        earned = max(0.0, earned - 0.5 * len(analysis.wrong_objects))
    return earned / possible, f"{earned:g} of {possible:g} object and placement checks passed."


def efficiency_fraction(task: TaskDefinition, evidence: Evidence, config: ScoringConfig, goal: float | None) -> tuple[float | None, str]:
    if evidence.duration_seconds is None:
        return None, "Execution duration is unavailable."
    ratio = (evidence.duration_seconds + (evidence.decision_seconds or 0.0)) / max(task.max_duration_seconds, 1e-6)
    if ratio <= config.efficient_time_fraction:
        speed = 1.0
    else:
        span = max(1 - config.efficient_time_fraction, 1e-6)
        speed = max(0.2, 1 - 0.8 * (ratio - config.efficient_time_fraction) / span)
    return speed * (goal or 0.0), f"Movement and decisions used {ratio * 100:.0f}% of the time limit; credit scales with goal completion."


def component_fractions(task, evidence, config, verification, analysis, goal):
    correctness, correctness_text = object_correctness(task, evidence, verification, analysis)
    efficiency, efficiency_text = efficiency_fraction(task, evidence, config, goal)
    engaged = analysis.target_contacted is True or (goal or 0) > 0
    preservation = None
    preservation_text = "Initial or final scene snapshot is unavailable."
    if analysis.measured:
        preservation = 1.0 if engaged else 0.0
        preservation_text = "Preservation credit is earned only after the robot engages the task."
    quality = None
    quality_text = "Contact events are unavailable."
    if analysis.quality is not None:
        quality = analysis.quality * (goal or 0.0)
        quality_text = "Contact quality, scaled by goal completion."
    return {
        "goal_completion": (goal, "Task-specific physical checks."),
        "object_correctness": (correctness, correctness_text),
        "scene_preservation": (preservation, preservation_text),
        "interaction_quality": (quality, quality_text),
        "efficiency": (efficiency, efficiency_text),
    }


def decide_outcome(task, evidence, verification, analysis) -> str:
    if not task.criteria or task.unresolved or evidence.initial_snapshot is None or evidence.final_snapshot is None:
        return "NOT_EVALUABLE"
    if evidence.execution_status == "CANCELLED":
        return "NOT_EVALUABLE"
    if evidence.execution_status == "FAILED" and not evidence.action_count:
        return "NOT_EVALUABLE"
    if any(item.status == "NOT_MEASURED" for item in verification):
        return "NOT_EVALUABLE"
    if analysis.critical:
        return "UNSAFE"
    if all(item.status == "PASS" for item in verification):
        return "SUCCESS"
    if any(item.status in ("PASS", "PARTIAL") for item in verification):
        return "PARTIAL_SUCCESS"
    return "FAILURE"


def score_components(fractions, analysis: InteractionAnalysis, config: ScoringConfig):
    available = sum(config.weights[name] for name, (value, _) in fractions.items() if value is not None)
    total_weight = sum(config.weights.values())
    coverage = round(100 * available / total_weight, 1)
    scale = 100 / available if available else 0
    base = 0.0 if available else None
    components: dict[str, Component] = {}
    for name, (value, detail) in fractions.items():
        weight = config.weights[name]
        effective = weight * scale if value is not None else 0.0
        points = None if value is None else effective * float(np.clip(value, 0, 1))
        if points is not None:
            base += points
        components[name] = Component(
            status="MEASURED" if value is not None else "NOT_MEASURED",
            weight=weight,
            effective_weight=round(effective, 2),
            base_points=None if points is None else round(points, 2),
            points=None if points is None else round(points, 2),
            detail=detail,
        )
    total = sum(item.penalty for item in analysis.incidents)
    remaining = allocate(components, "scene_preservation", sum(item.penalty for item in analysis.incidents if item.type != "PROTECTED_COLLISION"))
    remaining += allocate(components, "interaction_quality", sum(item.penalty for item in analysis.incidents if item.type == "PROTECTED_COLLISION"))
    remaining = allocate(components, "interaction_quality", remaining)
    return components, base, total, coverage


def allocate(components: dict[str, Component], name: str, amount: float) -> float:
    component = components[name]
    if component.points is None or amount <= 0:
        return amount
    used = min(component.points, amount)
    component.points = round(component.points - used, 2)
    component.penalty_points = round(component.penalty_points + used, 2)
    return amount - used


def final_score(base, penalties, outcome, evidence, config):
    if base is None:
        return None, []
    score = float(np.clip(base - penalties, 0, 100))
    caps = []
    if outcome == "UNSAFE" and score > config.unsafe_cap:
        score = config.unsafe_cap
        caps.append(f"Critical unsafe interaction: maximum {config.unsafe_cap:g}")
    elif evidence.execution_status == "TIMED_OUT" and outcome != "SUCCESS" and score > config.timeout_cap:
        score = config.timeout_cap
        caps.append(f"Timeout: maximum {config.timeout_cap:g}")
    return round(score, 2), caps


def coverage_entry(status: str, detail: str, source: str) -> dict[str, str]:
    return {"status": status, "detail": detail, "source": source}


def scene_understanding(task, evidence, verification, analysis):
    measured = bool(verification) and all(item.status != "NOT_MEASURED" for item in verification)
    selection = "NOT_MEASURED"
    if analysis.target_contacted is not None:
        selection = "PASS" if analysis.target_contacted and not analysis.wrong_objects else "FAIL"
    spatial = "NOT_MEASURED"
    if measured:
        spatial = "PASS" if all(item.status == "PASS" for item in verification) else "FAIL"
    physical = "NOT_MEASURED"
    if measured:
        physical = "PASS" if all(item.status == "PASS" for item in verification) else "PARTIAL" if any(item.status == "PARTIAL" for item in verification) else "FAIL"
    preservation = "NOT_MEASURED"
    if analysis.measured:
        preservation = "PASS" if not analysis.incidents else "FAIL"
    return {
        "perception": perception_entry(task, evidence),
        "object_selection": coverage_entry(selection, "Whether the robot contacted the requested object and avoided moving others.", "MuJoCo contacts"),
        "spatial_reasoning": coverage_entry(spatial, "Whether the final spatial relationship matched the instruction.", "MuJoCo poses"),
        "physical_manipulation": coverage_entry(physical, "Whether the requested physical result was achieved.", "MuJoCo poses and contacts"),
        "scene_preservation": coverage_entry(preservation, "Whether unrelated and protected objects stayed within tolerance.", "MuJoCo poses"),
    }


def perception_entry(task: TaskDefinition, evidence: Evidence) -> dict[str, str]:
    judge = evidence.video_judge or {}
    if judge.get("verdict") in ("PASS", "FAIL", "UNSURE"):
        status = "NOT_MEASURED" if judge["verdict"] == "UNSURE" else judge["verdict"]
        return coverage_entry(status, judge.get("reason") or "The video judge gave no reason.", "Run video judged by Claude")
    video = (evidence.vision or {}).get("video")
    if video and video.get("status"):
        return coverage_entry(video["status"], video["detail"], "Run video analysed with OWLv2")
    source = "Inferred from gripper motion"
    if not task.criteria or not evidence.approach:
        return coverage_entry("NOT_MEASURED", "No gripper-motion evidence is available.", "unavailable")
    target = task.criteria[0].object_id
    destinations = {item.destination_id for item in task.criteria}
    reached = {k: v for k, v in evidence.approach.get("first_reached_s", {}).items() if k not in destinations}
    distances = {k: v for k, v in evidence.approach.get("min_distance_cm", {}).items() if k not in destinations}
    threshold = evidence.approach.get("reach_threshold_cm", 6)
    if reached:
        first = min(reached, key=lambda name: (reached[name], name))
        if first == target:
            return coverage_entry("PASS", f"The gripper reached the {target} first, which suggests it located the requested object.", source)
        return coverage_entry("FAIL", f"The gripper reached the {first} before the {target}, which suggests a wrong first target.", source)
    if target not in distances or not distances:
        return coverage_entry("NOT_MEASURED", "The target's gripper distance is unavailable.", "unavailable")
    closest = min(distances, key=distances.get)
    if distances[closest] >= 15:
        return coverage_entry("NOT_MEASURED", f"The gripper never came within 15 cm of a candidate object (closest {distances[closest]:g} cm), so its intended target cannot be inferred.", source)
    if closest == target:
        return coverage_entry("PARTIAL", f"The gripper moved closest to the {target} ({distances[target]:g} cm) but did not come within {threshold:g} cm.", source)
    return coverage_entry("FAIL", f"The gripper moved closest to the {closest} ({distances[closest]:g} cm), not the {target} ({distances[target]:g} cm).", source)


def explain(task, evidence, verification, analysis, outcome):
    good: list[str] = []
    bad: list[str] = []
    advice: list[str] = []
    for item in verification:
        name = item.criterion.object_id
        for check in item.checks:
            if check.status == "PASS":
                good.append(f"{name}: {check.name.replace('_', ' ')}.")
            elif check.status == "FAIL" and not (check.name == "lifted" and item.status == "PASS"):
                bad.append(f"{name}: {check.detail}")
    for incident in analysis.incidents:
        bad.append(incident.explanation)
        advice.append(f"Protect or add clearance around the {incident.object_id}: {incident.explanation}")
    bad.extend(evidence.errors)
    if task.unresolved:
        advice.append(f"Reword '{task.instruction}' using an object and destination in the scene so it can be scored.")
    if evidence.execution_status == "CANCELLED":
        advice.append("This attempt was stopped early. Run it to completion to get a score.")
    if evidence.execution_status == "FAILED" and not evidence.action_count:
        advice.append("Retry this attempt when Dream-1 is available. It did not test robot behaviour.")
    distances = (evidence.approach or {}).get("min_distance_cm", {})
    for item in verification:
        if item.status == "PASS":
            continue
        name = item.criterion.object_id
        checks = {check.name: check for check in item.checks}
        closest = distances.get(name)
        if analysis.target_contacted is False:
            where = f" Its gripper got within {closest:g} cm." if closest is not None else ""
            advice.append(f"The robot never touched the {name}.{where} Move the {name} closer to one arm or name it more specifically.")
        elif "lifted" in checks and checks["lifted"].status == "FAIL":
            advice.append(f"The robot touched the {name} but did not lift it. Try a clearer grasp position, away from other objects.")
        elif "reached_destination" in checks and checks["reached_destination"].status == "FAIL":
            advice.append(f"The {name} was lifted but missed the {item.criterion.destination_id}: {checks['reached_destination'].detail}. Place the destination nearer the arm carrying it.")
        elif "released" in checks and checks["released"].status == "FAIL":
            advice.append(f"The {name} reached the target but was still held. Allow more time for the gripper to release it.")
    if outcome == "SUCCESS" and not analysis.incidents:
        duration = f" in {evidence.duration_seconds:g} s" if evidence.duration_seconds is not None else ""
        advice.append(f"This attempt succeeded cleanly{duration}. Repeat it from a shuffled layout to test reliability.")
    return good, bad, list(dict.fromkeys(advice))


def summary(task, outcome, score, analysis, evidence) -> str:
    label = outcome.replace("_", " ").lower()
    if outcome == "NOT_EVALUABLE":
        return f"This run could not be scored from the available evidence (execution: {evidence.execution_status})."
    count = len(analysis.incidents)
    disturbance = "no scored disturbances" if count == 0 else f"{count} scored disturbance{'s' if count != 1 else ''}"
    return f"Outcome: {label}. Score {score:g}/100 with {disturbance}."
