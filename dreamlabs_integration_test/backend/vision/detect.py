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


def choose_device(model, processor):
    if torch.backends.mps.is_available():
        try:
            model.to("mps")
            probe = processor(text=[["a photo of an object"]], images=Image.new("RGB", (64, 64)), return_tensors="pt").to("mps")
            with torch.no_grad():
                model(**probe)
            return "mps"
        except Exception:
            model.to("cpu")
    if torch.cuda.is_available():
        model.to("cuda")
        return "cuda"
    return "cpu"


def detect(model, processor, device, path, objects):
    image = Image.open(path).convert("RGB")
    inputs = processor(text=[[prompt(name) for name in objects]], images=image, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    outputs.logits = outputs.logits.cpu()
    outputs.pred_boxes = outputs.pred_boxes.cpu()
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
    device = choose_device(model, processor)
    cache = {}

    def run(path, objects):
        with open(path, "rb") as handle:
            key = (hashlib.sha256(handle.read()).hexdigest(), tuple(objects))
        if key not in cache:
            cache[key] = detect(model, processor, device, path, objects)
        return cache[key]

    results, timelines = {}, {}
    for item in job["items"]:
        results[item["task_id"]] = {camera: run(path, item["objects"]) for camera, path in item["frames"].items()}
        timelines[item["task_id"]] = [run(path, item["objects"]) for path in item.get("video_frames", [])]
    with open(output_path, "w") as handle:
        json.dump({"model": MODEL, "threshold": THRESHOLD, "device": device, "results": results, "timelines": timelines}, handle)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
