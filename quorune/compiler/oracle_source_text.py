from __future__ import annotations

import re
from typing import Iterable

from ..class_levels import CLASS_REMINDER_TEXT
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


def is_standalone_parenthetical_reminder(text: str) -> bool:
    """Return whether one complete source line contains reminder text only.

    Parenthetical text can be nested, and a source line may contain more than
    one parenthetical group.  Reject unbalanced delimiters and any material
    character outside a group so malformed or mixed Oracle text still reaches
    the fail-closed compiler boundary.
    """

    stripped = text.strip()
    if not stripped or stripped[0] != "(" or stripped[-1] != ")":
        return False
    depth = 0
    saw_group = False
    for character in stripped:
        if character == "(":
            depth += 1
            saw_group = True
        elif character == ")":
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0 and not character.isspace():
            return False
    return saw_group and depth == 0


def material_source_lines(
    text: str,
    *,
    ordinary_saga: bool = False,
) -> Iterable[tuple[str, str, SourceSpan]]:
    """Yield material Oracle rows while omitting the ordinary Saga reminder."""

    for line, span in source_lines(text):
        material_line = without_parenthetical_reminder(line)
        if (
            not material_line
            and is_standalone_parenthetical_reminder(line)
            and line != CLASS_REMINDER_TEXT
            and not line.casefold().startswith("({t}: add ")
        ):
            continue
        if (
            ordinary_saga
            and not material_line
            and _ORDINARY_SAGA_RULES_REMINDER.fullmatch(line) is not None
        ):
            continue
        yield line, material_line, span


__all__ = [
    "is_standalone_parenthetical_reminder",
    "material_source_lines",
    "source_lines",
    "without_parenthetical_reminder",
]
