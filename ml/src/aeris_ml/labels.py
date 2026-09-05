"""Common label vocabulary and conservative dataset-specific mappings."""

from __future__ import annotations

from enum import Enum


class CommonLabel(str, Enum):
    EMPTY = "EMPTY"
    HUMAN_STATIC = "HUMAN_STATIC"
    HUMAN_MOTION = "HUMAN_MOTION"
    NONHUMAN_MOTION = "NONHUMAN_MOTION"
    ENTER = "ENTER"
    EXIT = "EXIT"
    ACROSS = "ACROSS"
    UNKNOWN = "UNKNOWN"


_EMPTY = {
    "empty",
    "background",
    "empty_room",
    "empty room",
    "no_person",
    "no person",
    "gone",
}
_STATIC = {
    "standing",
    "stand",
    "stationary",
    "sitting",
    "sit",
    "lying",
    "lie",
    "lie down",
    "no activity",
    "no_activity",
}
_MOTION = {
    "walking",
    "walk",
    "running",
    "run",
    "fall",
    "falling",
    "jumping",
    "gesture",
    "gestures",
    "mobile",
    "approach",
    "departure",
    "toward_pi",
    "away_from_pi",
    "box",
    "circle",
    "clean",
    "pickup",
    "pick up",
    "sit down",
    "stand up",
    "sittingupdown",
}
_NONHUMAN = {"pet", "robot", "irobot", "fan", "nonhuman", "non-human"}
_AUXILIARY_UNKNOWN = {
    "breathing",
    "breath",
    "respiration",
    "humanid",
    "human_id",
    "identity",
    "localization",
    "location",
    "proximity",
    "gait id",
}


def _normalise(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_")


def map_label(original_label: str | None, dataset_id: str | None = None) -> CommonLabel:
    """Map an original dataset label into the first AERIS common label set.

    The original label must always be preserved separately. Participant attributes
    such as gender, height, body type, clothing, and identity labels intentionally
    do not map to prediction targets here.
    """

    raw = _normalise(original_label)
    if not raw:
        return CommonLabel.UNKNOWN

    dataset = _normalise(dataset_id)

    if dataset in {"aeris", "aeris_native", "aeris_recorder"}:
        if raw == "enter":
            return CommonLabel.ENTER
        if raw == "exit":
            return CommonLabel.EXIT
        if raw.startswith("across"):
            return CommonLabel.ACROSS

    if raw in _AUXILIARY_UNKNOWN or any(token in raw for token in _AUXILIARY_UNKNOWN):
        return CommonLabel.UNKNOWN
    if raw in _NONHUMAN or any(token in raw for token in _NONHUMAN):
        return CommonLabel.NONHUMAN_MOTION
    if raw in {"enter", "entry"}:
        return CommonLabel.ENTER
    if raw in {"exit", "leave", "leaving"}:
        return CommonLabel.EXIT
    if raw.startswith("across"):
        return CommonLabel.ACROSS
    if raw in _EMPTY:
        return CommonLabel.EMPTY
    if raw in _STATIC:
        return CommonLabel.HUMAN_STATIC
    if raw in _MOTION:
        return CommonLabel.HUMAN_MOTION

    return CommonLabel.UNKNOWN
