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
from .models import Evidence, TaskDefinition, TaskResult
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

    def vision_items(self, run_id: str):
        with self.lock:
            return [(task, evidence) for task, evidence, _ in self._run_records(run_id) if evidence.initial_snapshot]

    def attach_vision(self, task_id: str, vision: dict):
        with self.lock:
            record = self.records.get(task_id)
            if record is None:
                return
            record[1].vision = vision
            self._save(*record)

    def has_run(self, run_id: str) -> bool:
        with self.lock:
            return any(record[0].run == run_id for record in self.records.values())

    def run_report(self, run_id: str):
        with self.lock:
            records = self._run_records(run_id)
        if not records:
            return None
        summary = summarize_run([(task, result, evidence.execution_status) for task, evidence, result in records])
        grid = any("grid" in (evidence.run_videos or evidence.videos) for _, evidence, _ in records)
        return run_report(summary, [task_report(*record) for record in records], f"/api/tasks/{run_id}/video/grid" if grid else None)

    def batches(self):
        with self.lock:
            names = list(dict.fromkeys(self.records[task_id][0].batch_id for task_id in self.order))
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
                if task.batch_id == batch_id
            ]
        if not rows and batch_id != self.current_batch:
            return None
        return {**aggregate(batch_id, rows), "name": self.names.get(batch_id)}
