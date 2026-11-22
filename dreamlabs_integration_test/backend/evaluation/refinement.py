"""Turn a finished run's suggestions into one-change tests, and compare the results with the original attempts."""

from statistics import mean
from typing import Any

from .insights import Record, attempt_facts

MAX_TESTS = 8  # the parallel runs a request can start
MEANINGFUL_POINTS = 10.0
CHECKABLE_DESTINATIONS = {"bowl", "plate"}
KIND_LABELS = {
    "control": "Unchanged control",
    "move_target": "Move object closer to an arm",
    "shorter_carry": "Shorten the carry",
    "clear_space": "Add clear space",
    "match_orientation": "Use a successful orientation",
    "clearer_name": "Name the object more clearly",
    "more_time": "Give more time",
}
STAGE_CHANGES = {
    "did_not_reach": ("move_target", "clearer_name"),
    "reached_no_touch": ("move_target", "clear_space", "clearer_name"),
    "touched_no_lift": ("clear_space", "match_orientation"),
    "missed_placement": ("shorter_carry", "more_time"),
    "not_released": ("more_time",),
    "unsafe": ("clear_space",),
}
FALLBACK_TEXT = {
    "move_target": "Move the object closer to one arm.",
    "shorter_carry": "Shorten the distance the object is carried.",
    "clear_space": "Add clear space around the object.",
    "match_orientation": "Test the object at the orientation from a successful attempt.",
    "clearer_name": "Name the object more specifically.",
    "more_time": "Allow more time to finish the task.",
}
WORDING = {
    "move_target": ("closer", "one arm"),
    "shorter_carry": ("nearer",),
    "clear_space": ("clearer", "away from other objects", "clearance"),
    "clearer_name": ("name it", "specific"),
    "more_time": ("more time",),
}


def test_slots(attempts: int) -> tuple[int, int]:
    """How many tests to run after a run of `attempts`: one per run plus a control, and how many are controls."""
    slots = min(MAX_TESTS, attempts + 1)
    return slots, 2 if slots >= 6 else 1


def judge_pending(evidence) -> bool:
    return (evidence.video_judge or {}).get("verdict") == "RUNNING" or (evidence.vision or {}).get("status") == "running"


def judge_verdict(evidence) -> str | None:
    """What the run's video judge said. The offline judge reports movement, so it never claims the task passed."""
    if judge_pending(evidence):
        return "RUNNING"
    if evidence.video_judge:
        return evidence.video_judge.get("verdict")
    vision = evidence.vision or {}
    video = vision.get("video") or {}
    if video.get("status") == "PASS":
        return "Target moved"
    if video.get("status") == "FAIL":
        return "Wrong object moved"
    if video.get("status") == "PARTIAL":
        return "Target and others moved" if video.get("target") in (video.get("moved_objects") or []) else "Target did not move"
    if video.get("status"):
        return "Not measured"
    return None if vision.get("status") == "done" else vision.get("status")


def blocked_reason(records: list[Record]) -> str | None:
    if not records:
        return "This run has no report yet."
    if any(task.refinement_of for task, _, _ in records):
        return "This run is already a suggestion test."
    if any(judge_pending(evidence) for _, evidence, _ in records):
        return "The benchmark is still analysing this run's videos."
    if any(evidence.execution_status == "CANCELLED" for _, evidence, _ in records):
        return "This run was stopped early. Run it to the end before testing suggestions."
    if all(result.outcome == "SUCCESS" for _, _, result in records):
        return "Every attempt succeeded, so there is no suggestion to test."
    return None


def suggestion_text(result, kind: str) -> str:
    words = WORDING.get(kind, ())
    for line in result.recommendations:
        if any(word in line.lower() for word in words):
            return line
    return FALLBACK_TEXT[kind]


def candidates(records: list[Record]) -> list[dict[str, Any]]:
    """Every possible one-change test: each failed attempt's main suggestion first, then the alternatives."""
    ordered = sorted(records, key=lambda record: record[0].attempt)
    facts = [(record, attempt_facts(*record)) for record in ordered]
    wins: dict[str, int] = {}
    for _, row in facts:
        if row["stage"] == "success":
            wins.setdefault(row["instruction"], row["attempt"])
    rounds: list[list[dict[str, Any]]] = []
    for (_, _, result), row in facts:
        if row["destination"] not in CHECKABLE_DESTINATIONS or not row["target"]:
            continue
        kinds = [kind for kind in STAGE_CHANGES.get(row["stage"], ()) if kind != "match_orientation" or row["instruction"] in wins]
        for position, kind in enumerate(kinds):
            while len(rounds) <= position:
                rounds.append([])
            rounds[position].append(
                {
                    "source_attempt": row["attempt"],
                    "source_scene": row["scene"],
                    "instruction": row["instruction"],
                    "target": row["target"],
                    "destination": row["destination"],
                    "kind": kind,
                    "label": KIND_LABELS[kind],
                    "suggestion": suggestion_text(result, kind),
                    "reference_attempt": wins.get(row["instruction"]) if kind == "match_orientation" else None,
                }
            )
    return [item for group in rounds for item in group]


def average(items: list[float]) -> float | None:
    return round(mean(items), 2) if items else None


def group_stats(rows: list[dict[str, Any]], score: str, success: str) -> dict[str, Any]:
    return {
        "average_score": average([row[score] for row in rows if row[score] is not None]),
        "successes": sum(bool(row[success]) for row in rows),
        "attempts": len(rows),
    }


def compare_tests(source_records: list[Record], test_records: list[Record]) -> dict[str, Any]:
    """Compare each test with the attempt it came from, after removing the change seen in the unchanged control."""
    before = {task.attempt: result for task, _, result in source_records}
    original_prompts = {task.attempt: task.robot_prompt or task.instruction for task, _, _ in source_records}
    rows = []
    for task, evidence, result in sorted(test_records, key=lambda record: record[0].attempt):
        origin = before.get(task.source_attempt)
        rows.append(
            {
                "attempt": task.attempt,
                "test": task.scene_number,
                "kind": task.test_kind,
                "label": KIND_LABELS.get(task.test_kind or "", task.test_kind),
                "change": task.test_change,
                "suggestion": task.test_suggestion,
                "instruction": task.instruction,
                "judge": task.judge,
                "prompt": task.robot_prompt or task.instruction,
                "judge_verdict": judge_verdict(evidence),
                "source_attempt": task.source_attempt,
                "before_score": origin.score if origin else None,
                "before_outcome": origin.outcome if origin else None,
                "before_success": bool(origin and origin.outcome == "SUCCESS"),
                "after_score": result.score,
                "after_outcome": result.outcome,
                "after_success": result.outcome == "SUCCESS",
                "control_change": None,
                "effect": None,
                "verdict": "control" if task.test_kind == "control" else "not_scored",
            }
        )
    controls = [row for row in rows if row["kind"] == "control" and row["before_score"] is not None and row["after_score"] is not None]
    for row in controls:
        row["effect"] = round(row["after_score"] - row["before_score"], 2)
    changes = [row for row in rows if row["kind"] != "control"]
    for row in changes:
        if row["before_score"] is None or row["after_score"] is None:
            continue
        paired = [item["effect"] for item in controls if item["source_attempt"] == row["source_attempt"]]
        drift = mean(paired or [item["effect"] for item in controls]) if controls else 0.0
        row["control_change"] = round(drift, 2) if controls else None
        row["effect"] = round(row["after_score"] - row["before_score"] - drift, 2)
        row["verdict"] = "helped" if row["effect"] >= MEANINGFUL_POINTS else "worse" if row["effect"] <= -MEANINGFUL_POINTS else "no_change"
    helped = sorted((row for row in changes if row["verdict"] == "helped"), key=lambda row: -row["effect"])
    control_gain = average([row["effect"] for row in controls])
    if helped:
        best = helped[0]
        recommendation = (
            f"Use this first: {best['change']}. It gained {best['effect']:g} points more than the unchanged control."
            if controls
            else f"Most promising: {best['change']}. It gained {best['effect']:g} points, but no unchanged control ran, so this could be normal variation."
        )
    elif control_gain is not None and control_gain >= MEANINGFUL_POINTS:
        recommendation = f"No suggestion beat the unchanged control, which itself gained {control_gain:g} points. Treat these differences as run-to-run variation."
    else:
        recommendation = "No suggestion clearly helped in this test."
    note = "Each suggestion was tried once, so repeat a promising change before relying on it. A change can make the task easier without improving Dream-1 itself."
    if any(row["kind"] == "control" and row["prompt"] != original_prompts.get(row["source_attempt"]) for row in rows):
        note += " These tests used a different task prompt from the original attempt. The control used the same new prompt, so compare each change with the control."
    return {
        "judge": next((row["judge"] for row in rows if row["judge"]), None),
        "judging": any(judge_pending(evidence) for _, evidence, _ in test_records),
        "before": group_stats(changes, "before_score", "before_success"),
        "after": group_stats(changes, "after_score", "after_success"),
        "control": group_stats([row for row in rows if row["kind"] == "control"], "after_score", "after_success"),
        "control_change": control_gain,
        "rows": rows,
        "best_attempt": helped[0]["attempt"] if helped else None,
        "recommendation": recommendation,
        "note": note,
    }
