"""The video judges a run can use, and the task prompt each one sends to Dream-1."""

import re

JUDGES = {
    "offline": {"name": "Offline judge", "detail": "Runs OWLv2 on this computer and checks which objects moved in the video. No Anthropic credits."},
    "claude": {"name": "Claude judge", "detail": "Claude reviews the video and decides whether the task finished. Uses Anthropic credits."},
}
VISUAL_NAMES = {
    "apple": ("red apple", r"(?:red\s+)?apple"),
    "banana": ("yellow banana", r"(?:yellow\s+)?banana"),
    "orange": ("orange fruit", r"orange(?:\s+fruit)?"),
    "water_bottle": ("water bottle", r"(?:water\s+)?bottle"),
    "bowl": ("white bowl", r"(?:white\s+)?bowl"),
    "plate": ("white plate", r"(?:white\s+)?plate"),
}
RELEASE = re.compile(r"\b(?:let go|release|rests?)\b", re.IGNORECASE)


def _visual(text, name):
    """Use the camera-visible name for an object's first mention."""
    if name not in VISUAL_NAMES:
        return text
    words, pattern = VISUAL_NAMES[name]
    return re.sub(rf"\b{pattern}\b", words, text, count=1, flags=re.IGNORECASE)


def task_prompt(instruction, goal, judge, clearer=False):
    """The prompt sent to Dream-1, keeping every condition the user wrote.
    @param instruction The task as the user wrote it.
    @param goal {"object", "target"}, or None when the instruction cannot be checked.
    @param judge "offline" or "claude".
    @param clearer Also use the object's visible name with the Claude judge.
    """
    text = instruction.strip().rstrip(".")
    if not goal:
        return text
    if judge == "offline":
        return _visual(_visual(text, goal["object"]), goal["target"])
    if clearer:
        text = _visual(text, goal["object"])
    return text if RELEASE.search(text) else f"{text} and let go so it rests there"
