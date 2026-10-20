from __future__ import annotations

import re

from ..enchant_spec import (
    AuraControllerRelation,
    AuraEnchantSubject,
    AuraRuleError,
    TypedEnchantSpec,
)
from ..target_forms import TargetCharacteristicForm
from ..target_numeric import (
    TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison,
)
from .grammar import enchant_restriction_text


_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "land",
        "planeswalker",
        "sorcery",
    }
)
_SUBTYPES = frozenset(
    {
        "clue",
        "equipment",
        "food",
        "forest",
        "giant",
        "island",
        "mountain",
        "plains",
        "spacecraft",
        "swamp",
        "vehicle",
        "wall",
    }
)
_COLOR_WORDS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_DYNAMIC_OR_OPEN = re.compile(
    r"(?:\bwithout\b|\bwith\b|\bmodified\b|\bpower\b|\bmana value\b|"
    r"\bnonbasic\b|\battached\b)",
)


def _object_relation(
    restriction: str,
) -> tuple[str, AuraControllerRelation]:
    for suffix, relation in (
        (" an opponent controls", AuraControllerRelation.OPPONENT),
        (" opponent controls", AuraControllerRelation.OPPONENT),
        (" you don't control", AuraControllerRelation.OPPONENT),
        (" you control", AuraControllerRelation.YOU),
    ):
        if restriction.endswith(suffix):
            return restriction[: -len(suffix)].strip(), relation
    return restriction, AuraControllerRelation.ANY


def _alternatives(restriction: str) -> tuple[str, ...]:
    normalized = restriction.replace(", or ", ", ")
    normalized = normalized.replace(" or ", ", ")
    return tuple(value.strip() for value in normalized.split(",") if value.strip())


def _forms(
    parts: tuple[str, ...],
) -> tuple[TargetCharacteristicForm, ...] | None:
    forms: list[TargetCharacteristicForm] = []
    for part in parts:
        if part in _CARD_TYPES:
            forms.append(TargetCharacteristicForm(types_all=(part,)))
        elif part in _SUBTYPES:
            forms.append(TargetCharacteristicForm(subtypes_any=(part,)))
        else:
            return None
    return tuple(forms)


def _fixed_characteristic_enchant(restriction: str) -> TypedEnchantSpec | None:
    values = None
    if restriction == 'creature without flying':
        values = {'keywords_none':('flying',)}
    match = re.fullmatch(r'creature with (?P<field>power|mana value) (?P<maximum>\d+) or less', restriction)
    if match is not None:
        maximum = int(match['maximum'])
        values = ({'mana_value_max':maximum} if match['field'] == 'mana value' else
            {'numeric_characteristic':TargetNumericCharacteristicSpec(
                TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_MOST, maximum)})
    if values is None:
        return None
    return TypedEnchantSpec(subject=AuraEnchantSubject.PERMANENT,
        types_all=('creature',), schema_version=3, **values)


def parse_typed_enchant_line(line: str) -> TypedEnchantSpec | None:
    """Parse closed player, public-card, and characteristic restrictions."""

    restriction = enchant_restriction_text(line)
    if restriction is None:
        return None
    fixed = _fixed_characteristic_enchant(restriction)
    if fixed is not None:
        return fixed
    if restriction == "player":
        return TypedEnchantSpec(subject=AuraEnchantSubject.PLAYER)
    if restriction == "opponent":
        return TypedEnchantSpec(
            subject=AuraEnchantSubject.PLAYER,
            player_relation=AuraControllerRelation.OPPONENT,
        )
    graveyard = re.fullmatch(
        r"(?P<card_type>creature|instant) card in a graveyard",
        restriction,
    )
    if graveyard is not None:
        return TypedEnchantSpec(
            subject=AuraEnchantSubject.GRAVEYARD_CARD,
            types_all=(graveyard.group("card_type"),),
        )
    if _DYNAMIC_OR_OPEN.search(restriction):
        return None

    restriction, relation = _object_relation(restriction)
    values: dict[str, object] = {
        "subject": AuraEnchantSubject.PERMANENT,
        "controller_relation": relation,
    }
    if restriction == "noncommander creature":
        values.update(types_all=("creature",), commander=False)
    elif restriction.startswith("non") and restriction.endswith(" creature"):
        excluded = restriction.removeprefix("non-").removeprefix("non")
        excluded = excluded.removesuffix(" creature").strip()
        if excluded in _COLOR_WORDS:
            values.update(
                types_all=("creature",),
                colors_none=(_COLOR_WORDS[excluded],),
            )
        elif excluded in _SUBTYPES:
            values.update(
                types_all=("creature",),
                subtypes_none=(excluded,),
            )
        else:
            return None
    elif restriction.endswith(" creature"):
        prefix = restriction[:-9].strip()
        color_parts = tuple(
            value.strip() for value in prefix.split(" or ")
        )
        if color_parts and all(value in _COLOR_WORDS for value in color_parts):
            values.update(
                types_all=("creature",),
                colors_any=tuple(_COLOR_WORDS[value] for value in color_parts),
            )
        elif prefix in {"basic", "legendary", "snow"}:
            values.update(
                types_all=("creature",),
                supertypes_any=(prefix,),
            )
        elif prefix in _CARD_TYPES:
            values.update(types_all=(prefix, "creature"))
        elif prefix in _SUBTYPES:
            values.update(
                types_all=("creature",),
                subtypes_any=(prefix,),
            )
        else:
            return None
    elif restriction.endswith(" land") and restriction[:-5] in {
        "basic",
        "snow",
    }:
        values.update(
            types_all=("land",),
            supertypes_any=(restriction[:-5],),
        )
    else:
        parts = _alternatives(restriction)
        if not parts:
            return None
        if all(value in _CARD_TYPES for value in parts):
            if len(parts) == 1:
                values["types_all"] = parts
            else:
                values["types_any"] = parts
        elif all(value in _SUBTYPES for value in parts):
            values["subtypes_any"] = parts
        else:
            forms = _forms(parts)
            if forms is None:
                return None
            values["characteristic_forms_any"] = forms
    try:
        return TypedEnchantSpec(**values)
    except (AuraRuleError, TypeError):
        return None


__all__ = ["parse_typed_enchant_line"]
