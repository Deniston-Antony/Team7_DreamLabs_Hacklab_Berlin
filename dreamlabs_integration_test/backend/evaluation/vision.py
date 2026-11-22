import json
import os
import queue
import subprocess
import threading
from pathlib import Path

MIN_VISIBLE_PIXELS = 150
TIMEOUT_S = 600
SETUP = "uv venv --python 3.12 .vision-venv && uv pip install --python .vision-venv/bin/python -r vision/requirements.txt"


class VisionQueue:
    def __init__(self, store, backend: Path):
        self.store = store
        self.python = backend / ".vision-venv" / "bin" / "python"
        self.script = backend / "vision" / "detect.py"
        self.cache = backend / ".vision-cache"
        self.jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, run_id: str, folder: Path):
        items = []
        for task, evidence in self.store.vision_items(run_id):
            if not evidence.frames:
                self.store.attach_vision(task.task_id, {"status": "unavailable", "detail": "No camera frames were saved for this attempt."})
                continue
            items.append(
                {
                    "task_id": task.task_id,
                    "attempt": task.attempt,
                    "frames": {camera: str(folder / name) for camera, name in evidence.frames.items()},
                    "objects": [item.object_id for item in evidence.initial_snapshot.objects],
                    "target": task.criteria[0].object_id if task.criteria else None,
                    "destination": task.criteria[0].destination_id if task.criteria else None,
                    "pixels": evidence.visible_pixels,
                }
            )
        if not items:
            return
        unavailable = None
        if os.environ.get("DREAM_VISION") == "off":
            unavailable = "The vision check is turned off."
        elif not self.python.exists():
            unavailable = f"The vision model is not installed. In backend, run: {SETUP}"
        if unavailable:
            for item in items:
                self.store.attach_vision(item["task_id"], {"status": "unavailable", "detail": unavailable})
            return
        for item in items:
            self.store.attach_vision(item["task_id"], {"status": "running"})
        self.jobs.put((run_id, folder, items))

    def _work(self):
        while True:
            run_id, folder, items = self.jobs.get()
            try:
                self._run(run_id, folder, items)
            except Exception as error:
                for item in items:
                    self.store.attach_vision(item["task_id"], {"status": "failed", "detail": f"{type(error).__name__}: {error}"})

    def _run(self, run_id, folder, items):
        job, output = folder / "vision-job.json", folder / "vision-result.json"
        job.write_text(json.dumps({"items": items}))
        environment = {**os.environ, "HF_HOME": str(self.cache), "TOKENIZERS_PARALLELISM": "false"}
        environment.pop("PYTHONPATH", None)
        if (self.cache / "hub" / "models--google--owlv2-base-patch16-ensemble").exists():
            environment["HF_HUB_OFFLINE"] = "1"
        done = subprocess.run(
            ["nice", "-n", "10", str(self.python), str(self.script), str(job), str(output)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            env=environment,
        )
        if done.returncode != 0:
            tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
            raise RuntimeError(tail[:300])
        data = json.loads(output.read_text())
        for item in items:
            self.store.attach_vision(item["task_id"], summarize(run_id, item, data))


def summarize(run_id, item, data):
    cameras = {}
    agree, total = 0, 0
    for camera, found in data["results"][item["task_id"]].items():
        objects = {}
        pixels = item["pixels"].get(camera, {})
        for name, detection in found["objects"].items():
            truth = pixels.get(name)
            visible = None if truth is None else truth >= MIN_VISIBLE_PIXELS
            if visible is not None:
                total += 1
                agree += visible == detection["detected"]
            objects[name] = {**detection, "simulator_visible": visible}
        cameras[camera] = {
            "url": f"/api/tasks/{run_id}/frames/{item['attempt']}/{camera}",
            "width": found["width"],
            "height": found["height"],
            "objects": objects,
        }
    def seen(name):
        return [camera for camera, value in cameras.items() if name and value["objects"].get(name, {}).get("detected")]

    target, destination = item["target"], item["destination"]
    return {
        "status": "done",
        "model": data["model"],
        "threshold": data["threshold"],
        "frame": "First camera frame of the attempt",
        "target": target,
        "target_detected_in": seen(target),
        "destination": destination,
        "destination_detected_in": seen(destination),
        "agreement": {"matching": agree, "total": total},
        "cameras": cameras,
    }
