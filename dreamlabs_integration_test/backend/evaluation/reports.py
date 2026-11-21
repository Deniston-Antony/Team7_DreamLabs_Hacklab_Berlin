from html import escape
from typing import Any

from .models import Evidence, TaskDefinition, TaskResult

STYLE = """
body{font:14px system-ui,sans-serif;color:#1c2024;background:#f4f5f6;margin:0;padding:24px}
main{max-width:980px;margin:auto}section{background:#fff;border:1px solid #dfe3e6;border-radius:8px;padding:16px;margin:0 0 14px}
h1{font-size:23px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px}table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:5px;border-bottom:1px solid #edf0f2;vertical-align:top}.score{font-size:34px;font-weight:700}.muted{color:#5f6871}video{width:100%;max-width:720px;display:block;margin:8px 0;background:#111}
"""


def task_report(task: TaskDefinition, evidence: Evidence, result: TaskResult) -> dict[str, Any]:
    return {
        "report_type": "task",
        "task": {
            "task_id": task.task_id,
            "batch_id": task.batch_id,
            "run_id": task.run,
            "attempt": task.attempt,
            "where": task.where,
            "scene": task.scene_id,
            "robot": task.robot_id,
            "instruction": task.instruction,
            "started_at": task.started_at,
            "protected_objects": task.protected_objects,
            "criteria": [item.model_dump() for item in task.criteria],
        },
        "execution": {
            "status": evidence.execution_status,
            "duration_seconds": evidence.duration_seconds,
            "wall_seconds": evidence.wall_seconds,
            "action_count": evidence.action_count,
            "model_calls": evidence.model_calls,
            "errors": evidence.errors,
            "hidden_attempts": evidence.hidden_attempts,
        },
        "result": result.model_dump(),
        "evidence": {
            "source": evidence.source,
            "initial_snapshot": evidence.initial_snapshot.model_dump() if evidence.initial_snapshot else None,
            "final_snapshot": evidence.final_snapshot.model_dump() if evidence.final_snapshot else None,
            "contact_timeline": [item.model_dump() for item in (evidence.contacts or [])[:200]],
            "contact_events_total": None if evidence.contacts is None else len(evidence.contacts),
            "final_contacts": evidence.final_contacts,
            "videos": [f"/api/tasks/{task.run}/video/{name}" for name in evidence.videos if name == f"attempt-{task.attempt}"],
            "approach": evidence.approach,
            "model_output_keys": evidence.model_output_keys,
        },
    }


def rows(items: list[list[Any]]) -> str:
    return "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>" for row in items)


def bullets(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>" if items else '<p class="muted">None.</p>'


def page(title: str, body: str) -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{escape(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>'


def task_body(report: dict[str, Any]) -> str:
    task, execution, result = report["task"], report["execution"], report["result"]
    score = "Not scored" if result["score"] is None else f"{result['score']:g}/100"
    components = [
        [name.replace("_", " "), item["status"], "n/a" if item["points"] is None else f"{item['points']:g} / {item['effective_weight']:g}", item["detail"]]
        for name, item in result["components"].items()
    ]
    penalties = [[item["type"], item["object_id"], item["severity"], f"-{item['penalty']:g}", item["explanation"]] for item in result["incidents"]]
    checks = [
        [item["criterion"]["type"], check["name"], check["status"], check["detail"]]
        for item in result["verification"]
        for check in item["checks"]
    ]
    understanding = [[name.replace("_", " "), item["status"], item["detail"]] for name, item in result["scene_understanding"].items()]
    videos = "".join(f'<video controls preload="metadata" src="{escape(url, quote=True)}"></video>' for url in report["evidence"]["videos"])
    start_state = report["evidence"]["initial_snapshot"] or {"objects": []}
    end_state = {item["object_id"]: item for item in (report["evidence"]["final_snapshot"] or {"objects": []})["objects"]}
    moved = [[item["object_id"], item["position"], end_state.get(item["object_id"], {}).get("position", "n/a")] for item in start_state["objects"]]
    body = f"""
    <section><h1>Attempt {task['attempt']} report</h1><p class="muted">{escape(task['task_id'])} · {escape(task['scene'])} · {escape(task['robot'])}</p>
    <p><strong>{escape(task['instruction'])}</strong></p><div class="score">{escape(score)}</div>
    <p>{escape(result['outcome'])} · coverage {result['coverage']:g}% · scoring {escape(result['config_version'])}</p><p>{escape(result['summary'])}</p></section>
    <section><h2>Execution</h2><table>{rows([['Status', execution['status']], ['Simulated duration (s)', execution['duration_seconds']], ['Actions', execution['action_count']], ['Dream-1 calls', execution['model_calls']]])}</table></section>
    <section><h2>Score breakdown</h2><table><tr><th>Component</th><th>Status</th><th>Points</th><th>Detail</th></tr>{rows(components)}</table></section>
    <section><h2>Penalties</h2>{'<table><tr><th>Type</th><th>Object</th><th>Severity</th><th>Points</th><th>Explanation</th></tr>' + rows(penalties) + '</table>' if penalties else '<p class="muted">No penalties.</p>'}</section>
    <section><h2>Task checks</h2><table><tr><th>Type</th><th>Check</th><th>Status</th><th>Detail</th></tr>{rows(checks)}</table></section>
    <section><h2>Scene understanding evidence</h2><table><tr><th>Category</th><th>Status</th><th>Detail</th></tr>{rows(understanding)}</table></section>
    <section><h2>What went well</h2>{bullets(result['went_well'])}<h2>What went wrong</h2>{bullets(result['went_wrong'])}<h2>Recommendations</h2>{bullets(result['recommendations'])}<h2>Missing evidence</h2>{bullets(result['missing_evidence'])}</section>
    <section><h2>Evidence</h2>{videos}<h3>Initial and final object positions</h3><table><tr><th>Object</th><th>Initial (m)</th><th>Final (m)</th></tr>{rows(moved)}</table>
    <p class="muted">Contact events recorded: {escape(str(report['evidence']['contact_events_total']))}</p></section>
    """
    return body


def task_html(report: dict[str, Any]) -> str:
    return page(f"Task report {report['task']['task_id']}", task_body(report))


def run_report(summary: dict[str, Any], attempts: list[dict[str, Any]], grid_url: str | None) -> dict[str, Any]:
    return {"report_type": "run", "run": summary, "grid_url": grid_url, "attempts": attempts}


def run_html(report: dict[str, Any]) -> str:
    run = report["run"]
    score = "Not scored" if run["final_score"] is None else f"{run['final_score']:g}/100"
    table = [[item["attempt"], item["where"], item["execution_status"], item["outcome"], "n/a" if item["score"] is None else item["score"]] for item in run["attempts"]]
    grid = f'<video controls preload="metadata" src="{escape(report["grid_url"], quote=True)}"></video>' if report.get("grid_url") else ""
    body = f"""
    <section><h1>Final run report</h1><p><strong>{escape(run['instruction'])}</strong></p><div class="score">{escape(score)}</div>
    <p>{escape(run['outcome'])} · {run['attempts_succeeded']} of {run['attempts_total']} attempts succeeded</p>{grid}{bullets(run['recommendations'])}
    <table><tr><th>Attempt</th><th>Where</th><th>Execution</th><th>Outcome</th><th>Score</th></tr>{rows(table)}</table></section>
    """ + "".join(task_body(item) for item in report["attempts"])
    return page(f"Run report {run['run_id']}", body)


def batch_html(report: dict[str, Any]) -> str:
    score = "Not scored" if report["batch_score"] is None else f"{report['batch_score']:g}/100"
    tasks = [[item["task_id"], item["instruction"], item["scene_id"], item["execution_status"], item["outcome"], "n/a" if item["score"] is None else item["score"]] for item in report["tasks"]]
    counts = [[name.replace("_", " ").title(), report[name]] for name in ("total_tasks", "successful", "partial", "failed", "unsafe", "timed_out", "not_evaluable", "total_disturbances", "total_penalties")]
    stats = [[name.replace("_", " ").title(), "n/a" if report[name] is None else report[name]] for name in ("average_score", "median_score", "highest_score", "lowest_score", "success_rate", "score_coverage")]
    body = f"""
    <section><h1>Benchmark report</h1><p class="muted">{escape(report['batch_id'])}</p><div class="score">{escape(score)}</div><p>{escape(report['sample_note'])}</p></section>
    <section><h2>Overview</h2><table>{rows(counts)}{rows(stats)}</table></section>
    <section><h2>Tasks</h2><table><tr><th>Task</th><th>Instruction</th><th>Scene</th><th>Execution</th><th>Outcome</th><th>Score</th></tr>{rows(tasks)}</table></section>
    <section><h2>Common issues</h2><table>{rows([[item['type'].replace('_', ' '), item['count']] for item in report['common_failures']])}</table><h2>Recommendations</h2>{bullets(report['recommendations'])}</section>
    """
    return page(f"Benchmark report {report['batch_id']}", body)
