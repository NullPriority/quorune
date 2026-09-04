from __future__ import annotations

import re
from typing import Iterable

from .ir_model import SourceSpan


_ORDINARY_SAGA_RULES_REMINDER = re.compile(
    r"\(As this Saga enters and after your draw step, add a lore counter\. "
    r"Sacrifice after [IVXLCDM]+\.\)",
    re.IGNORECASE,
)


def source_lines(text: str) -> Iterable[tuple[str, SourceSpan]]:
    offset = 0
    for line_number, raw in enumerate(text.splitlines(keepends=True), 1):
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if stripped:
            left = len(line) - len(line.lstrip())
            yield stripped, SourceSpan(
                start=offset + left,
                end=offset + left + len(stripped),
                line=line_number,
            )
        offset += len(raw)
    if text and not text.splitlines(keepends=True):
        yield text, SourceSpan(0, len(text), 1)


def without_parenthetical_reminder(text: str) -> str:
    result: list[str] = []
    depth = 0
    for character in text:
        if character == "(":
            depth += 1
            continue
        if character == ")" and depth:
            depth -= 1
            continue
        if depth == 0:
            result.append(character)
    return "".join(result).strip()


def material_source_lines(
    text: str,
    *,
    ordinary_saga: bool = False,
) -> Iterable[tuple[str, str, SourceSpan]]:
    """Yield material Oracle rows while omitting the ordinary Saga reminder."""

    for line, span in source_lines(text):
        material_line = without_parenthetical_reminder(line)
        if (
            ordinary_saga
            and not material_line
            and _ORDINARY_SAGA_RULES_REMINDER.fullmatch(line) is not None
        ):
            continue
        yield line, material_line, span


__all__ = [
    "material_source_lines",
    "source_lines",
    "without_parenthetical_reminder",
]
