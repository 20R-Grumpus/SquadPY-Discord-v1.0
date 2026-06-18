"""Input validation helpers."""

import re

STEAMID_RE = re.compile(r"^\d{17}$")
EOSID_RE = re.compile(r"^[\w\d]{32}$")
LAYER_RE = re.compile(r"^[A-Za-z0-9]+_[A-Za-z0-9]+_v\d+$")


def is_valid_steamid(value: str) -> bool:
    return bool(STEAMID_RE.match(value or ""))


def is_valid_eosid(value: str) -> bool:
    return bool(EOSID_RE.match(value or ""))


def is_valid_layer_name(value: str) -> bool:
    return bool(LAYER_RE.match(value or ""))


def extract_steamids(text: str) -> list[str]:
    return re.findall(r"\d{17}", text or "")
