import json
import os
import queue
import subprocess
import threading
from pathlib import Path

import numpy as np
from PIL import Image

MIN_VISIBLE_PIXELS = 150
TIMEOUT_S = 900
VIDEO_SAMPLES = 10
MOVED_RATIO = 0.5
MIN_SEEN_FRAMES = 3
SETUP = "uv venv --python 3.12 .vision-venv && uv pip install --python .vision-venv/bin/python -r vision/requirements.txt"


def sample_video(path: Path, folder: Path, count: int = VIDEO_SAMPLES):
    import av

    folder.mkdir(parents=True, exist_ok=True)
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        rate = float(stream.average_rate or 20)
        frames = [frame.to_ndarray(format="rgb24") for frame in container.decode(stream)]
    if not frames:
        return [], []
    indices = np.unique(np.linspace(0, len(frames) - 1, count).round().astype(int))
    paths, times = [], []
    for number, index in enumerate(indices):
        target = folder / f"frame-{number:02d}.jpg"
        Image.fromarray(frames[index]).save(target, quality=90)
        paths.append(str(target))
        times.append(round(int(index) / rate, 1))
    return paths, times


def track_object(name, timeline):
    points = []
    for frame in timeline:
        box = frame["objects"].get(name, {}).get("box")
        if box:
            points.append(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, max(box[2] - box[0], box[3] - box[1])))
    if len(points) < MIN_SEEN_FRAMES:
        return {"seen": len(points), "change": None, "moved": False}
    early = np.array([point[:2] for point in points[:2]])
    base = np.median(early, axis=0)
    size = max(float(np.median([point[2] for point in points[:2]])), 24.0)
    changes = [float(np.linalg.norm(np.array(point[:2]) - base)) / size for point in points[1:]]
    end = float(np.linalg.norm(np.median(np.array([point[:2] for point in points[-2:]]), axis=0) - base)) / size
    change = max(sorted(changes)[-2], end)
    return {"seen": len(points), "change": round(change, 2), "moved": change >= MOVED_RATIO}


def video_perception(item, timeline, times):
    target = item["target"]
    movable = set(item.get("movable", item["objects"]))
    objects = {name: track_object(name, timeline) for name in item["objects"]}
    moved = [name for name, value in objects.items() if value["moved"] and name in movable]
    count = len(timeline)
    result = {"frames": count, "times": times, "target": target, "objects": objects, "moved_objects": moved}
    if not target:
        others = ", ".join(moved) or "no tracked object"
        return {**result, "status": "NOT_MEASURED", "target_seen": 0, "detail": f"No target to check. The video shows {others} moving."}
    seen = objects.get(target, {}).get("seen", 0)
    result["target_seen"] = seen
    others = [name for name in moved if name != target]
    if seen < MIN_SEEN_FRAMES:
        return {**result, "status": "NOT_MEASURED", "detail": f"The {target} was located in only {seen} of {count} sampled video frames, so the video cannot confirm what the robot acted on."}
    if target in moved and not others:
        return {**result, "status": "PASS", "detail": f"The video shows the {target} moving, and no other tracked object moved. It was located in {seen} of {count} frames."}
    if target in moved:
        return {**result, "status": "PARTIAL", "detail": f"The video shows the {target} moving, but also {', '.join(others)}."}
    if others:
        return {**result, "status": "FAIL", "detail": f"The video shows {', '.join(others)} moving while the {target} stayed in place."}
    return {**result, "status": "PARTIAL", "detail": f"The {target} was located in {seen} of {count} frames, but the video shows it was not moved."}


class VisionQueue:
    def __init__(self, store, backend: Path):
        self.store = store
        self.python = backend / ".vision-venv" / "bin" / "python"
        self.script = backend / "vision" / "detect.py"
        self.cache = backend / ".vision-cache"
        self.jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, run_id: str, folder: Path, only_missing: bool = False) -> int:
        items = []
        for task, evidence in self.store.vision_items(run_id):
            current = evidence.vision or {}
            if only_missing and (current.get("video") or current.get("status") == "running"):
                continue
            frames = {camera: str(folder / name) for camera, name in evidence.frames.items() if (folder / name).is_file()}
            clip = folder / f"attempt-{task.attempt}.mp4"
            if not frames and not clip.is_file():
                self.store.attach_vision(task.task_id, {"status": "unavailable", "detail": "No camera frames or video were saved for this attempt."})
                continue
            items.append(
                {
                    "task_id": task.task_id,
                    "attempt": task.attempt,
                    "frames": frames,
                    "clip": str(clip) if clip.is_file() else None,
                    "objects": [item.object_id for item in evidence.initial_snapshot.objects],
                    "movable": [item.object_id for item in evidence.initial_snapshot.objects if item.movable],
                    "target": task.criteria[0].object_id if task.criteria else None,
                    "destination": task.criteria[0].destination_id if task.criteria else None,
                    "pixels": evidence.visible_pixels,
                }
            )
        if not items:
            return 0
        unavailable = None
        if os.environ.get("DREAM_VISION") == "off":
            unavailable = "The vision check is turned off."
        elif not self.python.exists():
            unavailable = f"The vision model is not installed. In backend, run: {SETUP}"
        if unavailable:
            for item in items:
                self.store.attach_vision(item["task_id"], {"status": "unavailable", "detail": unavailable})
            return 0
        for item in items:
            self.store.attach_vision(item["task_id"], {"status": "running"})
        self.jobs.put((run_id, folder, items))
        return len(items)

    def _work(self):
        while True:
            run_id, folder, items = self.jobs.get()
            try:
                self._run(run_id, folder, items)
            except Exception as error:
                for item in items:
                    self.store.attach_vision(item["task_id"], {"status": "failed", "detail": f"{type(error).__name__}: {error}"})

    def _run(self, run_id, folder, items):
        for item in items:
            item["video_frames"], item["video_times"] = [], []
            if item.get("clip"):
                try:
                    item["video_frames"], item["video_times"] = sample_video(Path(item["clip"]), folder / f"attempt-{item['attempt']}.video")
                except Exception:
                    item["video_frames"], item["video_times"] = [], []
        job, output = folder / "vision-job.json", folder / "vision-result.json"
        job.write_text(json.dumps({"items": items}))
        environment = {**os.environ, "HF_HOME": str(self.cache), "TOKENIZERS_PARALLELISM": "false", "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
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
    timeline = data.get("timelines", {}).get(item["task_id"], [])
    return {
        "status": "done",
        "model": data["model"],
        "device": data.get("device"),
        "threshold": data["threshold"],
        "frame": "First camera frame of the attempt",
        "target": target,
        "target_detected_in": seen(target),
        "destination": destination,
        "destination_detected_in": seen(destination),
        "agreement": {"matching": agree, "total": total},
        "cameras": cameras,
        "video": video_perception(item, timeline, item.get("video_times", [])) if timeline else None,
    }
