from __future__ import annotations

"""Closed Oracle grammar for bounded combat runtime metadata."""

import re
from typing import Any, Mapping

from ..ability_fragments import ability_fragment_to_dict
from ..declaration_fragments import ATTACHED_GOAD_CAPABILITY, ATTACHED_GOAD_HANDLER_ID, AttachedGoadSpec
from ..semantic_runtime.combat_metadata import (
    GOAD_PROHIBITION_EVENT,
    GOAD_PROHIBITION_HANDLER_ID,
)


CombatMetadataTemplate = tuple[str, Mapping[str, Any], str]


_CONTROLLER_CREATURE_GOAD_PROHIBITION = re.compile(
    r"^Creatures you control can't be goaded\.?$",
    re.IGNORECASE,
)
_ATTACHED_GOAD = re.compile(
    r'(?P<subject>(?P<relation>Enchanted|Equipped) creature) '
    r'(?:is goaded|(?P<prefix>[^.\n"]+?)(?:,)? and is goaded)\.?',
    re.IGNORECASE,
)


def static_goad_prohibition_handler(
    text: str,
) -> CombatMetadataTemplate | None:
    """Lower the exact controller-creature goad prohibition."""

    if _CONTROLLER_CREATURE_GOAD_PROHIBITION.fullmatch(text.strip()) is None:
        return None
    return (
        "controller-creature-goad-prohibition-v1",
        {
            "handler_id": GOAD_PROHIBITION_HANDLER_ID,
            "schema_version": 1,
            "event": GOAD_PROHIBITION_EVENT,
            "affected_controller": "source_controller",
            "affected_card_type": "creature",
        },
        "combat.goad.prohibition.controller_creatures",
    )


def attached_goad_handler(text: str, *, source_name: str):
    match = _ATTACHED_GOAD.fullmatch(text.strip())
    if match is None:
        return None
    subtype = "aura" if match["relation"].casefold() == "enchanted" else "equipment"
    rule = {
        "handler_id": ATTACHED_GOAD_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "fragment": ability_fragment_to_dict(AttachedGoadSpec(subtype)),
    }
    if match["prefix"] is None:
        return "static-attached-goad-v1", rule, (ATTACHED_GOAD_CAPABILITY,)

    from .continuous_templates import attached_fixed_characteristics_handler
    from .declaration_nodes import fixed_static_declaration_grant_handler
    from .fixed_characteristic_settings import fixed_characteristic_setting_handler

    prefix = re.sub(r", (?=(?:has|must be blocked)\b)", " and ", match["prefix"], count=1, flags=re.IGNORECASE)
    clause = f"{match['subject']} {prefix}."
    compiled = (
        attached_fixed_characteristics_handler(clause, source_name=source_name)
        or fixed_static_declaration_grant_handler(clause, source_name=source_name)
        or fixed_characteristic_setting_handler(clause, source_name=source_name)
    )
    if compiled is None:
        return None
    _, handlers, dependencies = compiled
    handlers = handlers if isinstance(handlers, tuple) else (handlers,)
    return "static-attached-goad-composition-v1", (*handlers, rule), tuple(sorted({*dependencies, ATTACHED_GOAD_CAPABILITY}))


__all__ = [
    "CombatMetadataTemplate",
    "attached_goad_handler",
    "static_goad_prohibition_handler",
]
