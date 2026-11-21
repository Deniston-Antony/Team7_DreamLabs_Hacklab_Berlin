from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any

from .models import TaskDefinition, TaskResult


def group_scores(rows: list[tuple[TaskDefinition, TaskResult]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[TaskResult]] = defaultdict(list)
    for task, result in rows:
        groups[getattr(task, key)].append(result)
    output = []
    for name, items in sorted(groups.items()):
        scored = [item.score for item in items if item.score is not None]
        output.append(
            {
                "name": name,
                "tasks": len(items),
                "evaluable": len(scored),
                "average_score": round(mean(scored), 2) if scored else None,
                "successes": sum(item.outcome == "SUCCESS" for item in items),
            }
        )
    return output


def summarize_run(rows: list[tuple[TaskDefinition, TaskResult, str]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row[0].attempt)
    first = ordered[0][0]
    results = [result for _, result, _ in ordered]
    scored = [result.score for result in results if result.score is not None]
    outcomes = [result.outcome for result in results]
    evaluable = [item for item in outcomes if item != "NOT_EVALUABLE"]
    successes = outcomes.count("SUCCESS")
    if not evaluable:
        outcome = "NOT_EVALUABLE"
    elif "UNSAFE" in evaluable:
        outcome = "UNSAFE"
    elif successes == len(evaluable):
        outcome = "SUCCESS"
    elif successes or "PARTIAL_SUCCESS" in evaluable:
        outcome = "PARTIAL_SUCCESS"
    else:
        outcome = "FAILURE"
    advice = []
    if len(ordered) > 1 and evaluable:
        advice.append(f"{successes} of {len(evaluable)} scored attempts succeeded for '{first.instruction}'.")
        if 0 < successes < len(evaluable):
            best = max((row for row in ordered if row[1].score is not None), key=lambda row: row[1].score)
            advice.append(f"Compare attempt {best[0].attempt} with the failed attempts to see which approach worked.")
    if len(evaluable) < len(ordered):
        advice.append(f"{len(ordered) - len(evaluable)} attempt(s) could not be scored and should be re-run.")
    return {
        "run_id": first.run,
        "batch_id": first.batch_id,
        "instruction": first.instruction,
        "scene_id": first.scene_id,
        "robot_id": first.robot_id,
        "started_at": first.started_at,
        "outcome": outcome,
        "final_score": round(mean(scored), 2) if scored else None,
        "best_score": max(scored) if scored else None,
        "worst_score": min(scored) if scored else None,
        "attempts_total": len(ordered),
        "attempts_scored": len(evaluable),
        "attempts_succeeded": successes,
        "attempts": [
            {
                "attempt": task.attempt,
                "where": task.where,
                "task_id": task.task_id,
                "execution_status": status,
                "outcome": result.outcome,
                "score": result.score,
                "penalties": result.total_penalties,
                "summary": result.summary,
            }
            for task, result, status in ordered
        ],
        "recommendations": advice,
    }


def aggregate(batch_id: str, rows: list[tuple[TaskDefinition, TaskResult, str]]) -> dict[str, Any]:
    grouped: dict[str, list[tuple[TaskDefinition, TaskResult, str]]] = {}
    for row in rows:
        grouped.setdefault(row[0].run, []).append(row)
    runs = sorted((summarize_run(items) for items in grouped.values()), key=lambda item: item["started_at"] or 0, reverse=True)
    started = [item["started_at"] for item in runs if item["started_at"] is not None]
    pairs = [(task, result) for task, result, _ in rows]
    evaluable = [(task, result) for task, result in pairs if result.outcome != "NOT_EVALUABLE" and result.score is not None]
    scores = [result.score for _, result in evaluable]
    weight = sum(task.weight for task, _ in evaluable)
    batch_score = round(sum(task.weight * result.score for task, result in evaluable) / weight, 2) if weight else None
    outcomes = Counter(result.outcome for _, result in pairs)
    incidents = [incident for _, result in pairs for incident in result.incidents]
    penalties: dict[str, dict[str, float]] = {}
    for incident in incidents:
        row = penalties.setdefault(incident.type, {"count": 0, "points": 0})
        row["count"] += 1
        row["points"] += incident.penalty
    failures: Counter[str] = Counter(incident.type for incident in incidents)
    for _, result in pairs:
        for item in result.verification:
            for check in item.checks:
                if check.status == "FAIL":
                    failures[check.name] += 1
    timed_out = sum(status == "TIMED_OUT" for *_, status in rows)
    total = len(rows)
    quality = [result.components["interaction_quality"].points for _, result in evaluable if result.components["interaction_quality"].points is not None]
    recommendations = []
    if total < 5:
        recommendations.append("Run at least 5 tasks before comparing performance; this is a small run-level sample.")
    if failures:
        recommendations.append(f"Focus next on the most common issue: {failures.most_common(1)[0][0].replace('_', ' ')}.")
    if outcomes["NOT_EVALUABLE"]:
        recommendations.append("Re-run tasks that could not be evaluated.")
    return {
        "batch_id": batch_id,
        "first_started_at": min(started) if started else None,
        "last_started_at": max(started) if started else None,
        "total_runs": len(runs),
        "runs": runs,
        "total_tasks": total,
        "successful": outcomes["SUCCESS"],
        "partial": outcomes["PARTIAL_SUCCESS"],
        "failed": outcomes["FAILURE"],
        "unsafe": outcomes["UNSAFE"],
        "timed_out": timed_out,
        "not_evaluable": outcomes["NOT_EVALUABLE"],
        "batch_score": batch_score,
        "score_coverage": round(100 * len(evaluable) / total, 1) if total else 0,
        "success_rate": round(100 * outcomes["SUCCESS"] / len(evaluable), 1) if evaluable else None,
        "average_score": round(mean(scores), 2) if scores else None,
        "median_score": round(median(scores), 2) if scores else None,
        "highest_score": max(scores) if scores else None,
        "lowest_score": min(scores) if scores else None,
        "total_penalties": round(sum(incident.penalty for incident in incidents), 2),
        "total_disturbances": len(incidents),
        "average_interaction_points": round(mean(quality), 2) if quality else None,
        "penalty_breakdown": penalties,
        "common_failures": [{"type": name, "count": count} for name, count in failures.most_common(5)],
        "by_robot": group_scores(pairs, "robot_id"),
        "by_scene": group_scores(pairs, "scene_id"),
        "tasks": [
            {
                "task_id": task.task_id,
                "instruction": task.instruction,
                "scene_id": task.scene_id,
                "robot_id": task.robot_id,
                "execution_status": status,
                "outcome": result.outcome,
                "score": result.score,
                "coverage": result.coverage,
                "penalties": result.total_penalties,
                "report_url": f"/api/reports/tasks/{task.task_id}",
            }
            for task, result, status in rows
        ],
        "recommendations": recommendations,
        "sample_note": "Run-level benchmark only. A small number of trials does not establish statistically meaningful performance.",
    }
