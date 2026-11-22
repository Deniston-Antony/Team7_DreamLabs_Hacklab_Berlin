import hashlib
import json
import re
import sys
import warnings

import torch
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor

MODEL = "google/owlv2-base-patch16-ensemble"
THRESHOLD = 0.10
PROMPTS = {
    "apple": "a red apple",
    "banana": "a yellow banana",
    "orange": "an orange fruit",
    "water_bottle": "a water bottle",
    "bowl": "a white bowl",
    "plate": "a white plate",
}


def prompt(object_id):
    if object_id in PROMPTS:
        return f"a photo of {PROMPTS[object_id]}"
    if object_id.startswith("block_"):
        return "a photo of a blue block"
    words = [word for word in re.findall(r"[a-z]+", object_id.lower()) if word not in ("stl", "split")]
    return f"a photo of a {' '.join(words) or 'object'}"


def detect(model, processor, path, objects):
    image = Image.open(path).convert("RGB")
    inputs = processor(text=[[prompt(name) for name in objects]], images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
    # OWLv2 pads images to a square, so boxes scale by the longer side.
    side = max(image.size)
    found = processor.post_process_object_detection(outputs, threshold=0.01, target_sizes=torch.tensor([[side, side]]))[0]
    result = {"width": image.width, "height": image.height, "objects": {}}
    for index, name in enumerate(objects):
        scores = found["scores"][found["labels"] == index]
        boxes = found["boxes"][found["labels"] == index]
        if len(scores) == 0:
            result["objects"][name] = {"score": 0.0, "detected": False, "box": None}
            continue
        best = int(scores.argmax())
        score = float(scores[best])
        box = [round(float(value), 1) for value in boxes[best]]
        result["objects"][name] = {"score": round(score, 3), "detected": score >= THRESHOLD, "box": box if score >= THRESHOLD else None}
    return result


def main(job_path, output_path):
    warnings.filterwarnings("ignore")
    job = json.load(open(job_path))
    processor = Owlv2Processor.from_pretrained(MODEL, use_fast=False)
    model = Owlv2ForObjectDetection.from_pretrained(MODEL).eval()
    cache = {}
    results = {}
    for item in job["items"]:
        cameras = {}
        for camera, path in item["frames"].items():
            with open(path, "rb") as handle:
                key = (hashlib.sha256(handle.read()).hexdigest(), tuple(item["objects"]))
            if key not in cache:
                cache[key] = detect(model, processor, path, item["objects"])
            cameras[camera] = cache[key]
        results[item["task_id"]] = cameras
    with open(output_path, "w") as handle:
        json.dump({"model": MODEL, "threshold": THRESHOLD, "results": results}, handle)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
