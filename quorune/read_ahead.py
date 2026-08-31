from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import re
from typing import Any


READ_AHEAD_MECHANIC_ID = "read ahead"
READ_AHEAD_ENTRY_HANDLER_ID = "replacement.entry.read-ahead.v1"
READ_AHEAD_CAPABILITIES = (
    "counter.producer.saga_lore",
    "state_based.saga_final_chapter",
)

_CHAPTER_PREFIX = re.compile(
    r"^(?P<symbols>[IVX]+(?:\s*,\s*[IVX]+)*)\s*(?:—|–|-)\s*",
    re.IGNORECASE,
)
_ROMAN_CHAPTERS = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
}


@dataclass(frozen=True, slots=True)
class SagaChapterLine:
    """One printed ordinary Saga chapter carrier and its effect body."""

    chapters: tuple[int, ...]
    body: str

    def __post_init__(self) -> None:
        chapters = tuple(self.chapters)
        if (
            not chapters
            or chapters != tuple(sorted(set(chapters)))
            or any(type(value) is not int or value < 1 for value in chapters)
            or not isinstance(self.body, str)
            or not self.body.strip()
        ):
            raise ValueError("Saga chapter line is malformed")
        object.__setattr__(self, "chapters", chapters)
        object.__setattr__(self, "body", self.body.strip())


def saga_chapter_line(value: str) -> SagaChapterLine | None:
    """Parse one closed Roman-numeral Saga chapter prefix."""

    if not isinstance(value, str):
        return None
    match = _CHAPTER_PREFIX.match(value.strip())
    if match is None:
        return None
    body = value.strip()[match.end() :].strip()
    chapters = tuple(
        _ROMAN_CHAPTERS.get(symbol.strip().upper())
        for symbol in match.group("symbols").split(",")
    )
    if (
        not body
        or any(value is None for value in chapters)
        or tuple(value for value in chapters if value is not None)
        != tuple(sorted(set(value for value in chapters if value is not None)))
    ):
        return None
    return SagaChapterLine(
        chapters=tuple(value for value in chapters if value is not None),
        body=body,
    )


def saga_chapter_numbers(lines: Iterable[str]) -> tuple[int, ...]:
    """Parse one closed contiguous printed chapter-symbol vocabulary."""

    chapters: set[int] = set()
    for line in lines:
        normalized = str(line).strip()
        parsed = saga_chapter_line(normalized)
        if parsed is None:
            if _CHAPTER_PREFIX.match(normalized) is not None:
                return ()
            continue
        chapters.update(parsed.chapters)
    if not chapters:
        return ()
    ordered = tuple(sorted(chapters))
    if ordered != tuple(range(1, ordered[-1] + 1)):
        return ()
    return ordered


def read_ahead_entry_handler_descriptor(
    chapter_numbers: Sequence[int],
) -> dict[str, Any]:
    chapters = tuple(chapter_numbers)
    if (
        not chapters
        or any(type(value) is not int or value < 1 for value in chapters)
        or chapters != tuple(range(1, chapters[-1] + 1))
    ):
        raise ValueError(
            "Read Ahead requires contiguous positive chapter numbers"
        )
    return {
        "handler_id": READ_AHEAD_ENTRY_HANDLER_ID,
        "schema_version": 1,
        "event": "zone.change",
        "chapter_numbers": list(chapters),
        "counter_name": "lore",
        "rule_id": "714.3b",
    }


__all__ = [
    "READ_AHEAD_CAPABILITIES",
    "READ_AHEAD_ENTRY_HANDLER_ID",
    "READ_AHEAD_MECHANIC_ID",
    "SagaChapterLine",
    "read_ahead_entry_handler_descriptor",
    "saga_chapter_line",
    "saga_chapter_numbers",
]
