from collections import Counter, defaultdict
from statistics import mean
from typing import Any

import numpy as np

from .models import Evidence, TaskDefinition, TaskResult

STAGES = {
    "success": "Succeeded",
    "did_not_reach": "Did not reach the object",
    "reached_no_touch": "Reached but did not touch",
    "touched_no_lift": "Touched but did not lift",
    "missed_placement": "Lifted but missed the destination",
    "not_released": "Reached the destination but did not release",
    "unsafe": "Unsafe interaction",
    "not_scored": "Not scored",
    "other": "Did not complete",
}
LIFT_CM = 3.0
REACH_CM = 6.0
SAME_LAYOUT_CM = 1.5
MEANINGFUL_SCORE_GAP = 10.0

Record = tuple[TaskDefinition, Evidence, TaskResult]


def scene_of(task: TaskDefinition) -> int:
    return task.scene_number or task.attempt


def distance_cm(first, second) -> float:
    return round(float(np.linalg.norm(np.subtract(first[:2], second[:2]))) * 100, 1)


def stage_of(result: TaskResult, closest, lift, contact_s) -> str:
    if result.outcome == "NOT_EVALUABLE":
        return "not_scored"
    if result.outcome == "SUCCESS":
        return "success"
    if result.outcome == "UNSAFE":
        return "unsafe"
    checks = {check.name: check.status for item in result.verification for check in item.checks}
    if lift is not None and lift >= LIFT_CM:
        if checks.get("reached_destination") == "PASS" and checks.get("released") == "FAIL":
            return "not_released"
        return "missed_placement"
    if contact_s:
        return "touched_no_lift"
    if closest is None:
        return "other"
    return "reached_no_touch" if closest < REACH_CM else "did_not_reach"


def attempt_facts(task: TaskDefinition, evidence: Evidence, result: TaskResult) -> dict[str, Any]:
    criterion = task.criteria[0] if task.criteria else None
    target = criterion.object_id if criterion else None
    destination = criterion.destination_id if criterion else None
    start = evidence.initial_snapshot
    end = evidence.final_snapshot
    first = start.get(target) if start and target else None
    last = end.get(target) if end and target else None
    goal_start = start.get(destination) if start and destination else None
    goal_end = end.get(destination) if end and destination else None
    closest = (evidence.approach or {}).get("min_distance_cm", {}).get(target)
    lift = None
    if first is not None and target in evidence.max_heights:
        lift = round(max(0.0, (evidence.max_heights[target] - first.position[2]) * 100), 1)
    contact_s = None
    first_touch_s = None
    if evidence.contacts is not None and target:
        touches = [c for c in evidence.contacts if target in (c.a, c.b) and (c.a.endswith("_arm") or c.b.endswith("_arm"))]
        contact_s = round(sum(c.end_s - c.start_s for c in touches), 2)
        if touches and start and start.sim_time is not None:
            first_touch_s = round(max(0.0, min(c.start_s for c in touches) - start.sim_time), 1)
    nearest = None
    if first is not None and start:
        others = [
            distance_cm(first.position, item.position)
            for item in start.objects
            if item.object_id not in (target, destination) and item.position is not None
        ]
        nearest = min(others) if others else None
    video = (evidence.vision or {}).get("video") or {}
    judge = evidence.video_judge or {}
    judged = judge.get("verdict") in ("PASS", "FAIL", "UNSURE")
    return {
        "attempt": task.attempt,
        "task_id": task.task_id,
        "instruction": task.instruction,
        "scene": scene_of(task),
        "scene_label": task.scene_label or f"Scene {scene_of(task)}",
        "where": task.where,
        "target": target,
        "destination": destination,
        "score": result.score,
        "outcome": result.outcome,
        "execution_status": evidence.execution_status,
        "stage": (stage := stage_of(result, closest, lift, contact_s)),
        "stage_label": STAGES[stage],
        "closest_cm": closest,
        "lift_cm": lift,
        "contact_s": contact_s,
        "first_touch_s": first_touch_s,
        "start_to_goal_cm": distance_cm(first.position, goal_start.position) if first and goal_start else None,
        "final_to_goal_cm": distance_cm(last.position, goal_end.position) if last and goal_end else None,
        "nearest_object_cm": nearest,
        "movement_s": evidence.duration_seconds,
        "decision_s": evidence.decision_seconds,
        "wait_s": evidence.wait_seconds,
        "timer_s": None if evidence.duration_seconds is None else round(evidence.duration_seconds + (evidence.decision_seconds or 0.0) + (evidence.wait_seconds or 0.0), 2),
        "model_calls": evidence.model_calls,
        "disturbances": [incident.explanation for incident in result.incidents],
        "video_status": judge["verdict"] if judged else video.get("status"),
        "video_detail": judge.get("reason") if judged else video.get("detail"),
        "video_moved": None if judged else video.get("moved_objects"),
        "judge_verdict": judge["verdict"] if judged else None,
        "judge_agrees": judge.get("agrees_with_sim") if judged else None,
    }


def names(rows: list[dict[str, Any]]) -> str:
    numbers = [str(row["attempt"]) for row in rows]
    if len(numbers) == 1:
        return f"Attempt {numbers[0]}"
    return f"Attempts {', '.join(numbers[:-1])} and {numbers[-1]}"


def values(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [row[key] for row in rows if row.get(key) is not None]


def span(items: list[float], unit: str) -> str:
    if not items:
        return "an unmeasured amount"
    low, high = min(items), max(items)
    return f"{low:g} {unit}" if low == high else f"{low:g} to {high:g} {unit}"


def finding(title: str, evidence: str, action: str) -> dict[str, str]:
    return {"title": title, "evidence": evidence, "action": action}


def failure_finding(stage: str, rows: list[dict[str, Any]], wins: list[dict[str, Any]]) -> dict[str, str]:
    target = rows[0]["target"] or "object"
    destination = rows[0]["destination"] or "destination"
    who = names(rows)
    task = rows[0]["instruction"]
    closest = values(rows, "closest_cm")
    lifts = values(rows, "lift_cm")
    contacts = values(rows, "contact_s")
    if stage == "did_not_reach":
        return finding(
            f"The arm did not get to the {target}",
            f"{who} stayed {span(closest, 'cm')} from the {target}, outside the {REACH_CM:g} cm reach distance.",
            f"Test the {target} closer to one arm and check it is clear in the top camera.",
        )
    if stage == "reached_no_touch":
        return finding(
            f"The gripper stopped just short of the {target}",
            f"{who} came within {span(closest, 'cm')} but never touched it.",
            f"Compare these videos at the approach. Try a {target} position with more room around it for the fingers.",
        )
    if stage == "touched_no_lift":
        win_contact = values(wins, "contact_s")
        comparison = f" Successful attempts held contact for {mean(win_contact):.1f} s on average." if win_contact else ""
        return finding(
            f"The main failure is the grasp on the {target}",
            f"{who} touched it for {span(contacts, 's')} but lifted it only {span(lifts, 'cm')}.{comparison}",
            f"Focus on grasp closure: test the {target} at the orientations from the successful attempts before changing placement.",
        )
    if stage == "missed_placement":
        final = values(rows, "final_to_goal_cm")
        return finding(
            f"The {target} was lifted but missed the {destination}",
            f"{who} lifted it {span(lifts, 'cm')} and ended {span(final, 'cm')} from the {destination} centre.",
            f"The grasp is working here, so test shorter carries by moving the {destination} nearer the arm holding the {target}.",
        )
    if stage == "not_released":
        return finding(
            f"The {target} reached the {destination} but stayed in the gripper",
            f"{who} ended over the {destination} while still touching the robot.",
            "Check the end of these videos for a missing release, and give the run enough time to open the gripper.",
        )
    if stage == "unsafe":
        return finding(f"'{task}' pushed a protected object off the table", f"{who} caused a critical incident.", "Review these videos before using this layout again.")
    if stage == "not_scored":
        return finding(f"'{task}' could not be scored", f"{who} did not produce usable evidence.", "Re-run them so they count in the comparison.")
    return finding(f"'{task}' did not complete", f"{who} ended without enough movement evidence.", "Open their videos and re-run them.")


def compare_groups(wins: list[dict[str, Any]], losses: list[dict[str, Any]]) -> list[dict[str, str]]:
    output = []
    if not wins or not losses:
        return output
    target = wins[0]["target"] or "object"
    destination = wins[0]["destination"] or "destination"
    win_start, loss_start = values(wins, "start_to_goal_cm"), values(losses, "start_to_goal_cm")
    if win_start and loss_start and abs(mean(win_start) - mean(loss_start)) >= 3:
        easier = mean(win_start) < mean(loss_start)
        output.append(
            finding(
                f"{target.capitalize()} results differed with distance to the {destination}",
                f"Successful attempts started the {target} {mean(win_start):.1f} cm from the {destination}; unsuccessful ones started {mean(loss_start):.1f} cm away.",
                f"Include {'shorter' if easier else 'longer'} carries in the next test, then check whether the difference holds.",
            )
        )
    win_clear, loss_clear = values(wins, "nearest_object_cm"), values(losses, "nearest_object_cm")
    if win_clear and loss_clear and abs(mean(win_clear) - mean(loss_clear)) >= 3:
        detail = f"The nearest other object was {mean(win_clear):.1f} cm away in successful attempts and {mean(loss_clear):.1f} cm in unsuccessful ones."
        if mean(win_clear) > mean(loss_clear):
            output.append(
                finding(
                    f"Successful {target} attempts had more clear space",
                    detail,
                    f"Test the {target} with at least {mean(win_clear):.0f} cm clear, then check whether the difference holds.",
                )
            )
        else:
            output.append(
                finding(
                    f"Nearby objects did not explain the {target} failures",
                    detail,
                    f"The successful attempts had less clear space, so look at the {target}'s position and grasp first.",
                )
            )
    return output


def analyze_run(records: list[Record]) -> dict[str, Any]:
    facts = [attempt_facts(*record) for record in sorted(records, key=lambda item: item[0].attempt)]
    scored = [row for row in facts if row["stage"] != "not_scored"]
    wins = [row for row in scored if row["stage"] == "success"]
    losses = [row for row in scored if row["stage"] != "success"]
    stages = Counter(row["stage"] for row in facts)
    findings: list[dict[str, str]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in facts:
        if row["stage"] != "success":
            grouped[(row["stage"], row["instruction"])].append(row)
    for (stage, instruction), rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        findings.append(failure_finding(stage, rows, [row for row in wins if row["instruction"] == instruction]))
    for instruction in dict.fromkeys(row["instruction"] for row in scored):
        findings.extend(
            compare_groups(
                [row for row in wins if row["instruction"] == instruction],
                [row for row in losses if row["instruction"] == instruction],
            )
        )
    near = [row for row in losses if row["stage"] == "missed_placement" and row["final_to_goal_cm"] is not None]
    if len(near) > 1:
        best = min(near, key=lambda row: row["final_to_goal_cm"])
        findings.append(
            finding(
                f"Attempt {best['attempt']} was the closest miss",
                f"It lifted the {best['target']} {best['lift_cm']:g} cm and finished {best['final_to_goal_cm']:g} cm from the {best['destination']} centre.",
                f"Use attempt {best['attempt']}'s video as the first one to review for the final placement.",
            )
        )
    disturbed = [row for row in facts if row["disturbances"]]
    if disturbed:
        findings.append(
            finding(
                "Unrelated objects were disturbed",
                "; ".join(f"Attempt {row['attempt']}: {row['disturbances'][0]}" for row in disturbed[:3]),
                "Add clearance around those objects or mark them as protected in the instruction.",
            )
        )
    win_time = values(wins, "timer_s")
    limit_rows = [row for row in losses if row["execution_status"] == "TIMED_OUT"]
    if win_time and limit_rows:
        findings.append(
            finding(
                "Time was not the only limit" if max(win_time) < max(values(limit_rows, "timer_s") or [0]) * 0.8 else "Some attempts may need more time",
                f"Successful attempts used {span(win_time, 's')} of the time limit; {len(limit_rows)} other attempt(s) used the full limit without succeeding.",
                "Keep this limit if the successes finish well before it. Raise it only if successes finish near the limit.",
            )
        )
    disagreed = [row for row in facts if row["judge_agrees"] is False]
    if disagreed:
        findings.append(
            finding(
                "The video judge disagrees with the simulator",
                "; ".join(f"Attempt {row['attempt']}: simulator {'passed' if row['stage'] == 'success' else 'did not pass'} it, video judge says {row['judge_verdict']}. {row['video_detail']}" for row in disagreed[:3]),
                "Watch these videos before trusting their scores.",
            )
        )
    wrong_video = [row for row in facts if row["video_status"] == "FAIL" and row["video_moved"] is not None]
    if wrong_video:
        findings.append(
            finding(
                "The videos show the wrong object moving",
                "; ".join(f"Attempt {row['attempt']}: {row['video_detail']}" for row in wrong_video[:3]),
                "Name the target more specifically or separate it from similar nearby objects.",
            )
        )
    calls = values(scored, "model_calls")
    if calls and wins:
        findings.append(
            finding(
                "Reliability and cost",
                f"{len(wins)} of {len(scored)} scored attempts succeeded using {int(sum(calls))} Dream-1 calls, or {sum(calls) / len(wins):.0f} calls per success.",
                "Repeat the same layouts before treating this rate as stable; a small run can change a lot from random variation.",
            )
        )
    elif scored and not wins:
        findings.append(
            finding(
                "No attempt succeeded",
                f"0 of {len(scored)} scored attempts completed this task.",
                "Fix the main failure above first, then repeat the same scenes to measure the change.",
            )
        )
    return {
        "stages": [{"stage": stage, "label": STAGES[stage], "count": count} for stage, count in stages.most_common()],
        "attempts": facts,
        "findings": findings[:14],
    }


def layout_check(rows: list[Record]) -> dict[str, Any]:
    snapshots = [evidence.initial_snapshot for _, evidence, _ in rows if evidence.initial_snapshot]
    if len(snapshots) < 2:
        return {"checked": False, "same": None, "max_difference_cm": None}
    reference = {item.object_id: item.position for item in snapshots[0].objects if item.position}
    largest = 0.0
    for snapshot in snapshots[1:]:
        for item in snapshot.objects:
            if item.object_id in reference and item.position:
                largest = max(largest, float(np.linalg.norm(np.subtract(reference[item.object_id], item.position))) * 100)
    return {"checked": True, "same": largest <= SAME_LAYOUT_CM, "max_difference_cm": round(largest, 2)}


def average(items: list[float | None]) -> float | None:
    present = [item for item in items if item is not None]
    return round(mean(present), 2) if present else None


def compare_scenes(records: list[Record]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: item[0].attempt)
    instructions = list(dict.fromkeys(task.instruction for task, _, _ in ordered))
    scene_numbers = sorted({scene_of(task) for task, _, _ in ordered})
    facts = {task.task_id: attempt_facts(task, evidence, result) for task, evidence, result in ordered}
    scenes = []
    for number in scene_numbers:
        rows = [record for record in ordered if scene_of(record[0]) == number]
        results = [result for _, _, result in rows]
        first = rows[0]
        snapshot = first[1].initial_snapshot
        scenes.append(
            {
                "scene": number,
                "label": first[0].scene_label or f"Scene {number}",
                "average_score": average([result.score for result in results]),
                "attempts": len(results),
                "successes": sum(result.outcome == "SUCCESS" for result in results),
                "layout_check": layout_check(rows),
                "objects": [
                    {"object_id": item.object_id, "position": item.position, "movable": item.movable}
                    for item in (snapshot.objects if snapshot else [])
                ],
            }
        )
    tasks = []
    for instruction in instructions:
        rows = [record for record in ordered if record[0].instruction == instruction]
        cells = []
        for number in scene_numbers:
            match = [record for record in rows if scene_of(record[0]) == number]
            cells.append(
                {
                    "scene": number,
                    "score": average([record[2].score for record in match]),
                    "outcome": match[0][2].outcome if match else None,
                    "stage": facts[match[0][0].task_id]["stage_label"] if match else None,
                    "attempt": match[0][0].attempt if match else None,
                }
            )
        tasks.append(
            {
                "instruction": instruction,
                "average_score": average([record[2].score for record in rows]),
                "successes": sum(record[2].outcome == "SUCCESS" for record in rows),
                "attempts": len(rows),
                "cells": cells,
            }
        )
    ranked_tasks = sorted([item for item in tasks if item["average_score"] is not None], key=lambda item: (-item["average_score"], -item["successes"]))
    ranked_scenes = sorted([item for item in scenes if item["average_score"] is not None], key=lambda item: (-item["average_score"], -item["successes"]))
    improvements = []
    if len(ranked_tasks) > 1:
        best, worst = ranked_tasks[0], ranked_tasks[-1]
        improvements.append(
            finding(
                f"Dream-1 performed best on '{best['instruction']}'",
                f"It averaged {best['average_score']:g}/100 with {best['successes']} of {best['attempts']} successes. '{worst['instruction']}' averaged {worst['average_score']:g}/100 with {worst['successes']} of {worst['attempts']}.",
                f"Use the failed cells of '{worst['instruction']}' as the next test set.",
            )
        )
    if len(ranked_scenes) > 1:
        best, worst = ranked_scenes[0], ranked_scenes[-1]
        if best["average_score"] - worst["average_score"] >= MEANINGFUL_SCORE_GAP:
            improvements.append(
                finding(
                    f"Scene {best['scene']} was the easiest and Scene {worst['scene']} the hardest",
                    f"Scene {best['scene']} averaged {best['average_score']:g}/100 and Scene {worst['scene']} averaged {worst['average_score']:g}/100 across the same tasks.",
                    f"Compare object positions in these two scenes and repeat Scene {worst['scene']} to see whether its layout is the cause.",
                )
            )
        else:
            improvements.append(
                finding(
                    "The layouts made little difference",
                    f"Scene averages stayed within {best['average_score'] - worst['average_score']:.1f} points.",
                    "Use more varied layouts or more tasks to find what makes a scene harder.",
                )
            )
    swings = []
    for item in tasks:
        points = [cell for cell in item["cells"] if cell["score"] is not None]
        if len(points) > 1:
            high, low = max(points, key=lambda c: c["score"]), min(points, key=lambda c: c["score"])
            swings.append((high["score"] - low["score"], item, high, low))
    if swings:
        swing, item, high, low = max(swings, key=lambda value: value[0])
        if swing >= MEANINGFUL_SCORE_GAP:
            improvements.append(
                finding(
                    f"'{item['instruction']}' depends on the layout",
                    f"It scored {high['score']:g} in Scene {high['scene']} but {low['score']:g} in Scene {low['scene']} ({low['stage']}).",
                    f"Review attempt {high['attempt']} beside attempt {low['attempt']} to see what changed.",
                )
            )
    checks = [scene["layout_check"] for scene in scenes if scene["layout_check"]["checked"]]
    return {
        "comparable": len(instructions) > 1 or len(scene_numbers) > 1,
        "scenes": scenes,
        "tasks": tasks,
        "best_task": ranked_tasks[0]["instruction"] if ranked_tasks else None,
        "best_scene": ranked_scenes[0]["scene"] if ranked_scenes else None,
        "hardest_scene": ranked_scenes[-1]["scene"] if len(ranked_scenes) > 1 else None,
        "same_layouts_verified": all(check["same"] for check in checks) if checks else None,
        "improvements": improvements,
    }
