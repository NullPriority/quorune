from __future__ import annotations

"""Lower closed battlefield Cascade grants without parsing spells at runtime."""

import re

from ..object_predicate import ObjectQuerySpec
from ..semantic_runtime.battlefield_cascade_grants import (
    BattlefieldCascadeGrantHandler, BattlefieldCascadeGrantSpec,
)
from .cast_cost_modifier_templates import fixed_spell_predicate


_GRANT = re.compile(
    r'(?P<subject>(?:.+? )?spells you cast)(?P<hand> from your hand)?'
    r'(?: with mana value (?P<minimum>[0-9]+) or greater)? have '
    r'(?P<grant>cascade|"cascade, cascade\.?")\.?', re.IGNORECASE,
)


def battlefield_cascade_grant_handler(text: str):
    match = _GRANT.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    subject = match["subject"]
    predicate = (ObjectQuerySpec(commander=True) if subject.casefold() == "commander spells you cast"
                 else fixed_spell_predicate(subject))
    if predicate is None:
        return None
    try:
        spec = BattlefieldCascadeGrantSpec(
            predicate, "hand" if match["hand"] else None,
            int(match["minimum"]) if match["minimum"] is not None else None,
            1 if match["grant"].casefold() == "cascade" else 2,
        )
    except ValueError:
        return None
    handler = BattlefieldCascadeGrantHandler()
    return "battlefield-cascade-grant-v1", {
        "handler_id": handler.handler_id, "schema_version": handler.schema_version, "event": handler.event,
        "requires_complete_card_program": True, "grant": spec.to_dict(),
    }, handler.capability_dependencies
