"""Video judge: watches each finished attempt's MP4 and says whether the robot really did the task.

The backend saves every attempt to task_videos/<task_id>/attempt-N.mp4 (outcome.Session). When a task ends, main.py
hands it to `submit`; a background thread then reads the videos while the robot is already free for the next task.
For each video it samples frames (more from the end, where the result is visible), asks Claude to judge them against
the instruction, and writes the verdict into task["video_judge"]. It also compares that verdict with the simulator's
own check, so a disagreement between what the video shows and what the physics says is flagged.

Needs ANTHROPIC_API_KEY (or another Anthropic credential). Without one, verdicts are SKIPPED with the reason.
"""

import base64
import json
import os
import queue
import threading
import time
from pathlib import Path

import cv2

MODEL = os.environ.get("VIDEO_JUDGE_MODEL", "claude-opus-5-5")
FRAMES = 8  # frames sent per video
TAIL_SHARE = 0.5  # half of them come from the last 15% of the video, where the final state is visible
FRAME_WIDTH = 640
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL", "UNSURE"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "confidence", "reason"],
    "additionalProperties": False,
}
PROMPT = """You are judging a simulated robot. Two robot arms were given this instruction:

    {instruction}

The images are frames from the video of the attempt, in time order; the last frames show how it ended.
Decide whether the instruction was carried out by the end:
- PASS: the named object finally rests where the instruction says, released by the grippers.
- FAIL: it does not (wrong place, dropped, still held, knocked over, wrong object moved).
- UNSURE: the frames do not show enough to tell.
Give a confidence from 0 to 1 and one or two sentences naming what you see in the final frames."""

_jobs = queue.Queue()
_worker = None
_lock = threading.Lock()


def sample_frames(path, count=FRAMES):
    """Read `count` JPEG frames from a video: spread over it, with TAIL_SHARE of them from its last 15%.
    @return A list of JPEG bytes in time order (empty when the file cannot be read).
    """
    video = cv2.VideoCapture(str(path))
    total = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        video.release()
        return []
    tail = max(1, int(count * TAIL_SHARE))
    head = count - tail
    start_tail = int(total * 0.85)
    picks = [int(i * start_tail / max(head, 1)) for i in range(head)]
    picks += [start_tail + int(i * (total - 1 - start_tail) / max(tail - 1, 1)) for i in range(tail)]
    frames = []
    for index in sorted(set(picks)):
        video.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = video.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        if w > FRAME_WIDTH:
            frame = cv2.resize(frame, (FRAME_WIDTH, int(h * FRAME_WIDTH / w)))
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            frames.append(jpeg.tobytes())
    video.release()
    return frames


def ask_claude(instruction, frames, client=None):
    """Send the frames and instruction to Claude and return {"verdict", "confidence", "reason"}."""
    import anthropic

    client = client or anthropic.Anthropic()
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(f).decode()}}
        for f in frames
    ]
    content.append({"type": "text", "text": PROMPT.format(instruction=instruction)})
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        return {"verdict": "UNSURE", "confidence": 0.0, "reason": "the model declined to judge this video"}
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def judge_video(instruction, path, sim_success, ask=ask_claude):
    """Judge one video. `sim_success` is the simulator's verdict (True/False) or None when it had none."""
    frames = sample_frames(path)
    if not frames:
        return {"video": path.stem, "verdict": "SKIPPED", "reason": "the video could not be read"}
    result = {"video": path.stem, **ask(instruction, frames), "frames": len(frames)}
    if sim_success is not None and result["verdict"] in ("PASS", "FAIL"):
        result["sim_success"] = sim_success
        result["agrees_with_sim"] = (result["verdict"] == "PASS") == sim_success
    return result


def judge_task(task, folder, ask=ask_claude):
    """Judge every attempt video of a finished task and store the result in task["video_judge"]."""
    state = task["video_judge"]
    sims = {r["attempt"]: r.get("success") for r in task.get("attempt_results") or []}
    if not sims and task.get("outcome") in ("SUCCESS", "FAILURE"):
        sims = {1: task["outcome"] == "SUCCESS"}
    plan = task.get("plan") or []  # with several tasks, each run says which one (1-based) it ran
    instructions = {r["attempt"]: plan[r["task"] - 1]["instruction"]
                    for r in task.get("attempt_results") or [] if 0 < r.get("task", 0) <= len(plan)}
    for path in sorted(folder.glob("attempt-*.mp4"), key=lambda p: int(p.stem.split("-")[1])):
        number = int(path.stem.split("-")[1])
        instruction = instructions.get(number) or task["instruction"]
        try:
            verdict = judge_video(instruction, path, sims.get(number), ask)
        except Exception as error:  # one bad video must not stop the others
            verdict = {"video": path.stem, "verdict": "ERROR", "reason": f"{type(error).__name__}: {error}"}
        state["attempts"].append(verdict)
    verdicts = [a["verdict"] for a in state["attempts"]]
    state["passed"] = verdicts.count("PASS")
    state["disagreements"] = sum(1 for a in state["attempts"] if a.get("agrees_with_sim") is False)
    state["status"] = "DONE" if state["attempts"] else "SKIPPED"
    if not state["attempts"]:
        state["reason"] = "this task saved no attempt videos"
    state["finished_at"] = time.time()


def _run(videos_root, ask):
    while True:
        task = _jobs.get()
        task["video_judge"]["status"] = "RUNNING"
        try:
            judge_task(task, Path(videos_root) / task["task_id"], ask)
        except Exception as error:
            task["video_judge"].update(status="ERROR", reason=f"{type(error).__name__}: {error}")
        _jobs.task_done()


def has_credentials():
    return any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")) or \
        (Path.home() / ".config" / "anthropic").exists()


def submit(task, videos_root, ask=None):
    """Queue a finished task for judging in the background; returns at once.
    @param task The backend's task dictionary; task["video_judge"] is filled in as the judge works.
    @param videos_root Folder holding <task_id>/attempt-N.mp4.
    @param ask Override for the Claude call (tests).
    """
    global _worker
    if task.get("status") == "CANCELLED":
        task["video_judge"] = {"status": "SKIPPED", "reason": "the task was cancelled", "attempts": []}
        return
    if ask is None and not has_credentials():
        task["video_judge"] = {"status": "SKIPPED", "attempts": [],
                               "reason": "no Anthropic credential: set ANTHROPIC_API_KEY to judge videos"}
        return
    task["video_judge"] = {"status": "QUEUED", "model": MODEL, "attempts": []}
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, args=(videos_root, ask or ask_claude), daemon=True)
            _worker.start()
    _jobs.put(task)
