import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .batch import aggregate
from .config import DEFAULT_CONFIG, ScoringConfig
from .evaluator import evaluate_task
from .models import Evidence, TaskDefinition, TaskResult
from .reports import task_report

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
        self._load()

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

    def batches(self):
        with self.lock:
            counts: dict[str, int] = {}
            for task_id in self.order:
                batch = self.records[task_id][0].batch_id
                counts[batch] = counts.get(batch, 0) + 1
            return {
                "current_batch": self.current_batch,
                "batches": [{"batch_id": name, "tasks": count} for name, count in counts.items()],
            }

    def batch_report(self, batch_id: str):
        with self.lock:
            rows = [
                (task, result, evidence.execution_status)
                for task, evidence, result in (self.records[task_id] for task_id in self.order)
                if task.batch_id == batch_id
            ]
        return None if not rows and batch_id != self.current_batch else aggregate(batch_id, rows)
