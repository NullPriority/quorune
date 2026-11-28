from __future__ import annotations

from . import regex_working_set as re
from typing import Iterable

from .ir_model import SourceSpan
from ..characteristic_evaluation import type_parts


_ORDINARY_SAGA_RULES_REMINDER = re.compile(
    r"\(As this Saga enters and after your draw step, add a lore counter\."
    r"(?: Sacrifice after [IVXLCDM]+\.)?\)",
    re.IGNORECASE,
)
_TRANSFORM_REMINDER = re.compile(
    r"\(Transforms from [^)]+\.\)", re.IGNORECASE
)
_SIEGE_REMINDER = re.compile(
    r"\(As a Siege enters, choose an opponent to protect it\. You and others can attack it\. "
    r"When it['’]s defeated, exile it, then cast it transformed\.\)",
    re.IGNORECASE,
)
_MELD_PARTNER_NAME_WORD = r"(?:[A-Z][\w'’‐-]*|[dl]['’][A-Z][\w'’‐-]*|and|by|de|of|the)"
_MELD_PARTNER_REMINDER = re.compile(
    rf"\(Melds with [A-Z][\w'’‐-]*(?:,? {_MELD_PARTNER_NAME_WORD})*\.\)"
)
_DRYAD_ARBOR_REMINDER = (
    "(This land isn't a spell, it's affected by summoning sickness, and it "
    'has "{T}: Add {G}.")'
)
_LEGENDARY_SPELL_REMINDER = re.compile(
    r"\(You may cast a legendary (?P<spell>instant|sorcery) only if you control "
    r"a legendary creature or planeswalker\.\)",
    re.IGNORECASE,
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


def without_ability_word_label(text: str) -> str | None:
    """Expose a complete ability body, rejecting functional or condition labels."""
    match = re.fullmatch(r"(?P<label>[A-Z0-9][A-Za-z0-9, '’!?-]{0,79}) [—-] (?P<body>.+)", text, re.IGNORECASE)
    if match is None:
        return None if re.search(r" [—-] ", text) else text
    label = match['label'].casefold()
    if label in {'visit','solve','to solve','solved'} or label.startswith((
        'if ', 'when ', 'whenever ', 'as ', 'at ', 'this ', 'that ', 'you ', 'your ',
    )):
        return None
    return match['body']


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


def is_passive_meld_partner_reminder(text: str) -> bool:
    """Recognize a single partner-name clause in the printed reminder form."""
    return _MELD_PARTNER_REMINDER.fullmatch(text.strip()) is not None


def is_nonexecuting_standalone_reminder(
    text: str,
    *,
    transform: bool = False,
    meld: bool = False,
    dryad_arbor: bool = False,
    type_line: str = "",
    legendary_sorcery: bool = False,
    siege: bool = False,
) -> bool:
    """Recognize reminders whose authority is already carried structurally."""
    from ..rules.legendary_sorcery import REMINDER

    stripped = text.strip()
    if not is_standalone_parenthetical_reminder(stripped):
        return False
    legendary = _LEGENDARY_SPELL_REMINDER.fullmatch(stripped)
    if legendary is not None:
        types, _, supertypes = type_parts(type_line)
        return "legendary" in supertypes and legendary["spell"].casefold() in types
    return bool(
        _HYBRID_MANA_REMINDER.fullmatch(stripped)
        or _PHYREXIAN_MANA_REMINDER.fullmatch(stripped)
        or _TWO_BRID_MANA_REMINDER.fullmatch(stripped)
        or _COLORLESS_MANA_REMINDER.fullmatch(stripped)
        or _SNOW_MANA_REMINDER.fullmatch(stripped)
        or (transform and _TRANSFORM_REMINDER.fullmatch(stripped))
        or (siege and _SIEGE_REMINDER.fullmatch(stripped))
        or (meld and is_passive_meld_partner_reminder(stripped))
        or (dryad_arbor and stripped == _DRYAD_ARBOR_REMINDER)
        or (legendary_sorcery and stripped.casefold() == REMINDER)
    )


def material_source_lines(
    text: str,
    *,
    ordinary_saga: bool = False,
    layout: str = "",
    type_line: str = "",
) -> Iterable[tuple[str, str, SourceSpan]]:
    """Yield source-spanned material rows, skipping recognized reminders."""

    from ..rules.legendary_sorcery import is_legendary_sorcery

    legendary_sorcery = is_legendary_sorcery(type_line)
    from ..characteristic_evaluation import type_parts

    types, subtypes, _ = type_parts(type_line) if layout == "transform" else ((), (), ())
    siege = "battle" in types and "siege" in subtypes
    for line, span in source_lines(text):
        material_line = without_parenthetical_reminder(line)
        if (
            not material_line
            and is_nonexecuting_standalone_reminder(
                line,
                transform=layout == "transform",
                siege=siege,
                meld=layout == "meld",
                type_line=type_line,
                legendary_sorcery=legendary_sorcery,
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
    "is_passive_meld_partner_reminder",
    "is_standalone_parenthetical_reminder",
    "material_source_lines",
    "source_lines",
    "without_parenthetical_reminder",
]
