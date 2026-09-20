from __future__ import annotations

import re
from typing import Iterable

from .ir_model import SourceSpan


_ORDINARY_SAGA_RULES_REMINDER = re.compile(
    r"\(As this Saga enters and after your draw step, add a lore counter\. "
    r"Sacrifice after [IVXLCDM]+\.\)",
    re.IGNORECASE,
)
_TRANSFORM_REMINDER = re.compile(
    r"\(Transforms from [^)]+\.\)", re.IGNORECASE
)
_DRYAD_ARBOR_REMINDER = (
    "(This land isn't a spell, it's affected by summoning sickness, and it "
    'has "{T}: Add {G}.")'
)
_HYBRID_MANA_REMINDER = re.compile(
    r"\(\{(?P<first>[WUBRGC])/(?P<second>[WUBRGC])\} can be paid with "
    r"either \{(?P=first)\} or \{(?P=second)\}\.\)",
    re.IGNORECASE,
)
_PHYREXIAN_MANA_REMINDER = re.compile(
    r"\(\{(?P<color>[WUBRG])/P\} can be paid with either "
    r"\{(?P=color)\} or 2 life\.\)",
    re.IGNORECASE,
)
_TWO_BRID_MANA_REMINDER = re.compile(
    r"\(\{2/(?P<color>[WUBRG])\} can be paid with any two mana or with "
    r"\{(?P=color)\}\. This card['’]s mana value is \d+\.\)",
    re.IGNORECASE,
)
_COLORLESS_MANA_REMINDER = re.compile(
    r"\(\{C\} represents colorless mana\.\)", re.IGNORECASE
)
_SNOW_MANA_REMINDER = re.compile(
    r"\(\{S\} can be paid with one mana from a snow source\.\)",
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


def is_nonexecuting_standalone_reminder(
    text: str,
    *,
    transform: bool = False,
    dryad_arbor: bool = False,
) -> bool:
    """Recognize reminders whose authority is already carried structurally."""

    stripped = text.strip()
    if not is_standalone_parenthetical_reminder(stripped):
        return False
    return bool(
        _HYBRID_MANA_REMINDER.fullmatch(stripped)
        or _PHYREXIAN_MANA_REMINDER.fullmatch(stripped)
        or _TWO_BRID_MANA_REMINDER.fullmatch(stripped)
        or _COLORLESS_MANA_REMINDER.fullmatch(stripped)
        or _SNOW_MANA_REMINDER.fullmatch(stripped)
        or (transform and _TRANSFORM_REMINDER.fullmatch(stripped))
        or (dryad_arbor and stripped == _DRYAD_ARBOR_REMINDER)
    )


def material_source_lines(
    text: str,
    *,
    ordinary_saga: bool = False,
    layout: str = "",
    type_line: str = "",
) -> Iterable[tuple[str, str, SourceSpan]]:
    """Yield material Oracle rows while omitting the ordinary Saga reminder."""

    for line, span in source_lines(text):
        material_line = without_parenthetical_reminder(line)
        if (
            not material_line
            and is_nonexecuting_standalone_reminder(
                line,
                transform=layout == "transform",
                dryad_arbor=(
                    "land creature" in type_line.replace("—", " ").casefold()
                    and "forest dryad" in type_line.casefold()
                ),
            )
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
    "is_nonexecuting_standalone_reminder",
    "is_standalone_parenthetical_reminder",
    "material_source_lines",
    "source_lines",
    "without_parenthetical_reminder",
]
