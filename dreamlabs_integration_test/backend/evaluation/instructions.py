import re
from dataclasses import dataclass, field

from .models import Criterion

PROTECT = re.compile(
    r"\b(?:without|avoid|but not|do not|don'?t)\b\s*(?:moving|touching|disturbing|hitting|knocking(?:\s+over)?|move|touch|disturb|hit|knock(?:\s+over)?)?(.*)$"
)
ROTATE = re.compile(r"\b(?:rotate|turn|spin)\b")
DEGREES = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:degrees?|deg|°)")
STACK = re.compile(r"\bstack\b|\bon top of\b")
RELOCATE = re.compile(r"\b(?:move|push|slide|shift)\b.*\b(?:to|towards?)\s+(?:the\s+)?(left|right|front|back)\b")
PLACE = re.compile(r"\b(?:put|place|drop|set|move|bring|transfer)\b(.*?)\b(?:into|inside|onto|in|on|to)\b(.*)")


@dataclass
class ParsedInstruction:
    criteria: list[Criterion] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def aliases(object_id: str) -> set[str]:
    words = [word for word in re.findall(r"[a-z]+|\d+", object_id.lower()) if word != "stl"]
    names = {" ".join(words)}
    letters = [word for word in words if word.isalpha()]
    names.update(letters)
    if letters:
        names.add(" ".join(letters))
    return names


def mentions(text: str, object_ids: list[str]) -> list[str]:
    found: list[tuple[int, str]] = []
    claimed: dict[str, list[str]] = {}
    for object_id in object_ids:
        for alias in aliases(object_id):
            claimed.setdefault(alias, []).append(object_id)
    for object_id in object_ids:
        best = None
        for alias in sorted(aliases(object_id), key=len, reverse=True):
            if len(claimed[alias]) > 1:
                continue
            match = re.search(rf"\b{re.escape(alias)}\b", text)
            if match and (best is None or match.start() < best):
                best = match.start()
        if best is not None:
            found.append((best, object_id))
    return [object_id for _, object_id in sorted(found)]


def parse_instruction(instruction: str, object_ids: list[str]) -> ParsedInstruction:
    text = " ".join(instruction.lower().split())
    parsed = ParsedInstruction()
    main = text
    clause = PROTECT.search(text)
    if clause:
        main = text[: clause.start()]
        parsed.protected = mentions(clause.group(1), object_ids)

    def need(items: list[str], count: int, what: str) -> bool:
        if len(items) >= count:
            return True
        parsed.unresolved.append(f"Could not identify {what} in the scene.")
        return False

    if ROTATE.search(main):
        names = mentions(main, object_ids)
        degrees = DEGREES.search(main)
        upright = "upright" in main
        if not upright and not degrees:
            parsed.unresolved.append("Rotation tasks need 'upright' or an angle in degrees.")
        elif need(names, 1, "the object to rotate"):
            parsed.criteria.append(
                Criterion(
                    type="rotation",
                    object_id=names[0],
                    upright=upright,
                    angle_deg=float(degrees.group(1)) if degrees and not upright else None,
                )
            )
    elif STACK.search(main):
        names = mentions(main, object_ids)
        if need(names, 2, "both objects to stack"):
            parsed.criteria.append(Criterion(type="stacking", object_id=names[0], destination_id=names[1]))
    elif relocate := RELOCATE.search(main):
        names = mentions(main, object_ids)
        if need(names, 1, "the object to move"):
            parsed.criteria.append(Criterion(type="relocation", object_id=names[0], direction=relocate.group(1)))
    elif place := PLACE.search(main):
        subject = mentions(place.group(1), object_ids)
        target = [name for name in mentions(place.group(2), object_ids) if name not in subject[:1]]
        if need(subject, 1, "the object to move") and need(target, 1, "the destination"):
            parsed.criteria.append(Criterion(type="pick_place", object_id=subject[0], destination_id=target[0]))
    else:
        parsed.unresolved.append("This instruction does not match a supported task type.")

    used = {name for item in parsed.criteria for name in (item.object_id, item.destination_id) if name}
    parsed.protected = [name for name in parsed.protected if name not in used]
    return parsed
