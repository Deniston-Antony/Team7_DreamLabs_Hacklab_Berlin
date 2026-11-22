import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .batch import aggregate, summarize_run
from .config import DEFAULT_CONFIG, ScoringConfig
from .evaluator import evaluate_task
from .insights import analyze_run, compare_scenes
from .models import Evidence, TaskDefinition, TaskResult
from .refinement import compare_tests
from .reports import run_report, task_report

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class EvaluationStore:
    def __init__(self, folder: Path, config: ScoringConfig = DEFAULT_CONFIG):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.lock = threading.RLock()
        self.records: dict[str, tuple[TaskDefinition, Evidence, TaskResult]] = {}
        self.order: list[str] = []
        self.current_batch = f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.load_errors: list[str] = []
        self.names_path = self.folder / "benchmark_names.meta"
        self.names: dict[str, str] = self._load_names()
        self._load()

    def _load_names(self) -> dict[str, str]:
        try:
            data = json.loads(self.names_path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str)}

    def rename_batch(self, batch_id: str, name: str) -> str | None:
        with self.lock:
            known = batch_id == self.current_batch or any(record[0].batch_id == batch_id for record in self.records.values())
            if not known:
                return None
            self.names[batch_id] = name
            temporary = self.names_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.names))
            os.replace(temporary, self.names_path)
            return name

    def _path(self, task_id: str) -> Path:
        if not SAFE_ID.match(task_id):
            raise ValueError("Invalid task id.")
        return self.folder / f"{task_id}.json"

    def _load(self):
        files = sorted(self.folder.glob("*.json"), key=lambda path: path.stat().st_mtime)
        for path in files:
            try:
                data = json.loads(path.read_text())
                task = TaskDefinition.model_validate(data["task"])
                evidence = Evidence.model_validate(data["evidence"])
                if (evidence.video_judge or {}).get("verdict") == "RUNNING":
                    evidence.video_judge = {"verdict": "ERROR", "reason": "The backend restarted before this video was judged."}
                result = TaskResult.model_validate(data["result"])
            except (OSError, ValueError, KeyError) as error:
                self.load_errors.append(f"{path.name}: {type(error).__name__}")
                continue
            self.records[task.task_id] = (task, evidence, result)
            self.order.append(task.task_id)

    def _save(self, task: TaskDefinition, evidence: Evidence, result: TaskResult):
        path = self._path(task.task_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"task": task.model_dump(), "evidence": evidence.model_dump(), "result": result.model_dump()})
        )
        os.replace(temporary, path)

    def submit(self, task: TaskDefinition, evidence: Evidence) -> TaskResult:
        result = evaluate_task(task, evidence, self.config)
        with self.lock:
            self._save(task, evidence, result)
            if task.task_id not in self.records:
                self.order.append(task.task_id)
            self.records[task.task_id] = (task, evidence, result)
        return result

    def reevaluate(self, task_id: str) -> TaskResult | None:
        with self.lock:
            record = self.records.get(task_id)
        return None if record is None else self.submit(record[0], record[1])

    def result(self, task_id: str) -> TaskResult | None:
        with self.lock:
            record = self.records.get(task_id)
        return None if record is None else record[2]

    def report(self, task_id: str):
        with self.lock:
            record = self.records.get(task_id)
        return None if record is None else task_report(*record)

    def new_batch(self) -> str:
        with self.lock:
            self.current_batch = f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            return self.current_batch

    def _run_records(self, run_id: str):
        return sorted(
            (record for record in self.records.values() if record[0].run == run_id),
            key=lambda record: record[0].attempt,
        )

    def run_records(self, run_id: str):
        with self.lock:
            return self._run_records(run_id)

    def suggestion_results(self, run_id: str):
        """Every finished suggestion test of a run, newest first."""
        with self.lock:
            source = self._run_records(run_id)
            names = list(dict.fromkeys(self.records[item][0].run for item in self.order if self.records[item][0].refinement_of == run_id))
            output = []
            for name in reversed(names):
                rows = self._run_records(name)
                output.append({"run_id": name, "started_at": rows[0][0].started_at, "comparison": compare_tests(source, rows)})
            return output

    def attach_video_judge(self, task_id: str, verdict: dict):
        with self.lock:
            record = self.records.get(task_id)
            if record is None:
                return
            task, evidence, _ = record
            evidence.video_judge = verdict
            result = evaluate_task(task, evidence, self.config)  # the unscored perception note reads the verdict
            self.records[task_id] = (task, evidence, result)
            self._save(task, evidence, result)

    def run_clips(self, run_id: str):
        """(attempt, outcome, label) of each attempt in a run, for building a combined video."""
        with self.lock:
            records = self._run_records(run_id)
        return [
            (task.attempt, result.outcome, f"attempt {task.attempt} scene {task.scene_number or task.attempt}: {result.outcome.replace('_', ' ').lower()}")
            for task, _, result in records
        ]

    def has_run(self, run_id: str) -> bool:
        with self.lock:
            return any(record[0].run == run_id for record in self.records.values())

    def run_report(self, run_id: str):
        with self.lock:
            records = self._run_records(run_id)
        if not records:
            return None
        summary = summarize_run([(task, result, evidence.execution_status) for task, evidence, result in records])
        clips = {name for _, evidence, _ in records for name in (evidence.run_videos or evidence.videos)}
        grid = "grid" in clips or len(clips - {"grid"}) >= 2
        insights = analyze_run(records)
        comparison = compare_scenes(records)
        summary["recommendations"] = [item["action"] for item in insights["findings"]]
        report = run_report(summary, [task_report(*record) for record in records], f"/api/tasks/{run_id}/video/grid" if grid else None)
        output = {**report, "insights": insights, "comparison": comparison}
        source = records[0][0].refinement_of
        if source:
            with self.lock:
                original = self._run_records(source)
            output["refinement_of"] = source
            output["refinement"] = compare_tests(original, records)
        return output

    def batches(self):
        with self.lock:
            names = list(dict.fromkeys(self.records[task_id][0].batch_id for task_id in self.order if not self.records[task_id][0].refinement_of))
            if self.current_batch not in names:
                names.append(self.current_batch)
            output = []
            for name in names:
                report = self.batch_report(name)
                output.append(
                    {
                        "batch_id": name,
                        "name": self.names.get(name),
                        "is_current": name == self.current_batch,
                        "first_started_at": report["first_started_at"],
                        "last_started_at": report["last_started_at"],
                        "runs": report["total_runs"],
                        "attempts": report["total_tasks"],
                        "successful": report["successful"],
                        "batch_score": report["batch_score"],
                    }
                )
            output.sort(key=lambda item: item["first_started_at"] if item["first_started_at"] is not None else float("inf"))
            for number, item in enumerate(output, 1):
                item["number"] = number
            output.reverse()
            return {"current_batch": self.current_batch, "batches": output}

    def batch_report(self, batch_id: str):
        with self.lock:
            rows = [
                (task, result, evidence.execution_status)
                for task, evidence, result in (self.records[task_id] for task_id in self.order)
                if task.batch_id == batch_id and not task.refinement_of  # suggestion tests change the setup, so they stay out of the score
            ]
        if not rows and batch_id != self.current_batch:
            return None
        return {**aggregate(batch_id, rows), "name": self.names.get(batch_id)}
