from html import escape
from typing import Any

from .models import Evidence, TaskDefinition, TaskResult

STYLE = """
body{font:14px system-ui,sans-serif;color:#1c2024;background:#f4f5f6;margin:0;padding:24px}
main{max-width:980px;margin:auto}section{background:#fff;border:1px solid #dfe3e6;border-radius:8px;padding:16px;margin:0 0 14px}
h1{font-size:23px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px}table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:5px;border-bottom:1px solid #edf0f2;vertical-align:top}.score{font-size:34px;font-weight:700}.muted{color:#5f6871}video{width:100%;max-width:720px;display:block;margin:8px 0 3px;background:#111}.speeds{display:flex;gap:4px;margin-bottom:10px}.speeds button{padding:2px 7px;border:1px solid #aeb6bd;border-radius:4px;background:#fff;cursor:pointer}.speeds button.on{background:#1f5fbf;border-color:#1f5fbf;color:#fff}
"""


OLD_PERCEPTION_NOTES = ("Direct Dream-1 perception output", "Dream-1 internal perception output")
SPEED_SCRIPT = """<script>
document.querySelectorAll('video').forEach(function(video){
  var row=document.createElement('div');row.className='speeds';
  [1,1.5,2,4].forEach(function(rate){var button=document.createElement('button');button.type='button';button.textContent=rate+'x';
    if(rate===1)button.className='on';
    button.onclick=function(){video.playbackRate=rate;row.querySelectorAll('button').forEach(function(b){b.className=''});button.className='on'};row.appendChild(button)});
  video.insertAdjacentElement('afterend',row)});
</script>"""


def task_report(task: TaskDefinition, evidence: Evidence, result: TaskResult) -> dict[str, Any]:
    shown = result.model_dump()
    shown["missing_evidence"] = [item for item in shown["missing_evidence"] if not item.startswith(OLD_PERCEPTION_NOTES)]
    return {
        "report_type": "task",
        "task": {
            "task_id": task.task_id,
            "batch_id": task.batch_id,
            "run_id": task.run,
            "attempt": task.attempt,
            "where": task.where,
            "scene_number": task.scene_number or task.attempt,
            "scene_label": task.scene_label,
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
            "decision_seconds": evidence.decision_seconds,
            "wait_seconds": evidence.wait_seconds,
            "wall_seconds": evidence.wall_seconds,
            "action_count": evidence.action_count,
            "model_calls": evidence.model_calls,
            "errors": evidence.errors,
            "hidden_attempts": evidence.hidden_attempts,
        },
        "result": shown,
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
            "vision": evidence.vision,
        },
    }


def rows(items: list[list[Any]]) -> str:
    return "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>" for row in items)


def bullets(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>" if items else '<p class="muted">None.</p>'


def page(title: str, body: str) -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{escape(title)}</title><style>{STYLE}</style></head><body><main>{body}</main>{SPEED_SCRIPT}</body></html>'


def vision_section(vision: dict[str, Any] | None) -> str:
    if not vision:
        return ""
    if vision.get("status") != "done":
        return f'<section><h2>Vision check</h2><p class="muted">{escape(str(vision.get("detail") or vision.get("status")))}</p></section>'
    table = [
        [camera, name, "Yes" if item["detected"] else "No", item["score"], "n/a" if item["simulator_visible"] is None else "Yes" if item["simulator_visible"] else "No"]
        for camera, value in vision["cameras"].items()
        for name, item in value["objects"].items()
    ]
    agreement = vision["agreement"]
    return f"""<section><h2>Vision check (not scored)</h2><p>Target {escape(str(vision['target']))} detected in: {escape(', '.join(vision['target_detected_in']) or 'no camera')}.
    Matches simulator visibility in {agreement['matching']} of {agreement['total']} checks.</p><p class="muted">{escape(vision['model'])}. This checks the camera view, not what Dream-1 understood.</p>
    <table><tr><th>Camera</th><th>Object</th><th>Detected</th><th>Confidence</th><th>Simulator visible</th></tr>{rows(table)}</table></section>"""


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
    <section><h2>Execution</h2><table>{rows([['Status', execution['status']], ['Robot movement (s)', execution['duration_seconds']], ['Dream-1 decision time (s)', execution['decision_seconds']], ['Waiting for a Dream-1 seat (s)', execution['wait_seconds']], ['Actions', execution['action_count']], ['Dream-1 calls', execution['model_calls']]])}</table></section>
    <section><h2>Score breakdown</h2><table><tr><th>Component</th><th>Status</th><th>Points</th><th>Detail</th></tr>{rows(components)}</table></section>
    <section><h2>Penalties</h2>{'<table><tr><th>Type</th><th>Object</th><th>Severity</th><th>Points</th><th>Explanation</th></tr>' + rows(penalties) + '</table>' if penalties else '<p class="muted">No penalties.</p>'}</section>
    <section><h2>Task checks</h2><table><tr><th>Type</th><th>Check</th><th>Status</th><th>Detail</th></tr>{rows(checks)}</table></section>
    <section><h2>Scene understanding evidence</h2><table><tr><th>Category</th><th>Status</th><th>Detail</th></tr>{rows(understanding)}</table></section>
    <section><h2>What went well</h2>{bullets(result['went_well'])}<h2>What went wrong</h2>{bullets(result['went_wrong'])}<h2>Recommendations</h2>{bullets(result['recommendations'])}<h2>Missing evidence</h2>{bullets(result['missing_evidence'])}</section>
    <section><h2>Evidence</h2>{videos}<h3>Initial and final object positions</h3><table><tr><th>Object</th><th>Initial (m)</th><th>Final (m)</th></tr>{rows(moved)}</table>
    <p class="muted">Contact events recorded: {escape(str(report['evidence']['contact_events_total']))}</p></section>
    {vision_section(report['evidence'].get('vision'))}
    """
    return body


def task_html(report: dict[str, Any]) -> str:
    return page(f"Task report {report['task']['task_id']}", task_body(report))


def findings_html(items: list[dict[str, str]]) -> str:
    return "".join(
        f"<p><strong>{escape(item['title'])}</strong><br>{escape(item['evidence'])}<br><span class=\"muted\">Next: {escape(item['action'])}</span></p>"
        for item in items
    )


def comparison_html(comparison: dict[str, Any] | None) -> str:
    if not comparison or not comparison.get("comparable"):
        return ""
    heads = "".join(f"<th>Scene {item['scene']}</th>" for item in comparison["scenes"])
    body = "".join(
        f"<tr><td>{escape(item['instruction'])}</td>" + "".join(
            f"<td>{'n/a' if cell['score'] is None else cell['score']} {escape(str(cell['outcome'] or ''))}</td>" for cell in item["cells"]
        ) + f"<td>{'n/a' if item['average_score'] is None else item['average_score']}</td></tr>"
        for item in comparison["tasks"]
    )
    return f"<section><h2>Task and scene comparison</h2><table><tr><th>Task</th>{heads}<th>Average</th></tr>{body}</table>{findings_html(comparison['improvements'])}</section>"


def run_report(summary: dict[str, Any], attempts: list[dict[str, Any]], grid_url: str | None) -> dict[str, Any]:
    return {"report_type": "run", "run": summary, "grid_url": grid_url, "attempts": attempts}


def run_html(report: dict[str, Any]) -> str:
    run = report["run"]
    score = "Not scored" if run["final_score"] is None else f"{run['final_score']:g}/100"
    table = [[item["attempt"], item["where"], item["execution_status"], item["outcome"], "n/a" if item["score"] is None else item["score"]] for item in run["attempts"]]
    grid = f'<video controls preload="metadata" src="{escape(report["grid_url"], quote=True)}"></video>' if report.get("grid_url") else ""
    body = f"""
    <section><h1>Final run report</h1><p><strong>{escape(run['instruction'])}</strong></p><div class="score">{escape(score)}</div>
    <p>{escape(run['outcome'])} · {run['attempts_succeeded']} of {run['attempts_total']} attempts succeeded</p>{grid}
    <table><tr><th>Attempt</th><th>Where</th><th>Execution</th><th>Outcome</th><th>Score</th></tr>{rows(table)}</table>
    <h2>What the runs show</h2>{findings_html(report.get('insights', {}).get('findings', []))}</section>{comparison_html(report.get('comparison'))}
    """ + "".join(task_body(item) for item in report["attempts"])
    return page(f"Run report {run['run_id']}", body)


def batch_html(report: dict[str, Any]) -> str:
    score = "Not scored" if report["batch_score"] is None else f"{report['batch_score']:g}/100"
    tasks = [[item["task_id"], item["instruction"], item["scene_id"], item["execution_status"], item["outcome"], "n/a" if item["score"] is None else item["score"]] for item in report["tasks"]]
    counts = [[name.replace("_", " ").title(), report[name]] for name in ("total_tasks", "successful", "partial", "failed", "unsafe", "timed_out", "not_evaluable", "total_disturbances", "total_penalties")]
    stats = [[name.replace("_", " ").title(), "n/a" if report[name] is None else report[name]] for name in ("average_score", "median_score", "highest_score", "lowest_score", "success_rate", "score_coverage")]
    body = f"""
    <section><h1>{escape(report.get('name') or 'Benchmark report')}</h1><p class="muted">{escape(report['batch_id'])}</p><div class="score">{escape(score)}</div><p>{escape(report['sample_note'])}</p></section>
    <section><h2>Overview</h2><table>{rows(counts)}{rows(stats)}</table></section>
    <section><h2>Tasks</h2><table><tr><th>Task</th><th>Instruction</th><th>Scene</th><th>Execution</th><th>Outcome</th><th>Score</th></tr>{rows(tasks)}</table></section>
    <section><h2>Common issues</h2><table>{rows([[item['type'].replace('_', ' '), item['count']] for item in report['common_failures']])}</table><h2>Recommendations</h2>{bullets(report['recommendations'])}</section>
    """
    return page(f"Benchmark report {report['batch_id']}", body)
