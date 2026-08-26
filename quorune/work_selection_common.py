from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .util import stable_json


class WorkSelectionError(ValueError):
    pass


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkSelectionError(f"{label} must be an object")
    return value


def nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise WorkSelectionError(f"{label} must be a nonnegative integer")
    return value
