from __future__ import annotations

"""Lower closed battlefield Cascade grants without parsing spells at runtime."""

from . import regex_working_set as re

from ..object_predicate import ObjectQuerySpec
from ..public_history_query_model import SPELL_ORIGIN_HISTORY_CAPABILITY
from ..semantic_runtime.battlefield_cascade_grants import (
    BattlefieldCascadeGrantHandler, BattlefieldCascadeGrantSpec, ConditionalStackCascadeHandler,
    FIRST_EXILE_CASCADE_CAPABILITY,
)
from .cast_cost_modifier_templates import fixed_spell_predicate


_GRANT = re.compile(
    r'(?P<subject>(?:.+? )?spells you cast)(?P<hand> from your hand)?'
    r'(?: with mana value (?P<minimum>[0-9]+) or greater)? have '
    r'(?P<grant>cascade|"cascade, cascade\.?")\.?', re.IGNORECASE,
)


from quorune.compiler.public_state_queries import fixed_public_state_parts
from quorune.continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateFact


def stack_cascade_condition(text, *, source_name):
    """Recognize one source spell's Cascade behind a closed graveyard condition."""
    if "this spell has cascade" not in text.casefold():
        return None
    parts = fixed_public_state_parts(text, source_name=source_name)
    if parts is None:
        return None
    condition, body = parts
    if body.strip().removesuffix(".").casefold() != "this spell has cascade":
        return None
    if (condition.kind is not FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST
            or condition.fact is not FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_CARD_TYPES
            or condition.amount is None or condition.amount < 1):
        return None
    return condition


def conditional_stack_cascade_handler(text: str, *, source_name: str):
    condition = stack_cascade_condition(text, source_name=source_name)
    if condition is None:
        return None
    handler = ConditionalStackCascadeHandler()
    return "conditional-stack-cascade-v1", {
        "handler_id": handler.handler_id, "schema_version": handler.schema_version, "event": handler.event,
        "requires_complete_card_program": True, "source_condition": condition.to_dict(),
    }, handler.capability_dependencies


def battlefield_cascade_grant_handler(text: str):
    text = " ".join(text.split())
    if text.removesuffix(".").casefold() == "the first spell you cast from exile each turn has cascade":
        handler = BattlefieldCascadeGrantHandler()
        grant = BattlefieldCascadeGrantSpec(ObjectQuerySpec(), origin="exile", first_exile_cast=True)
        return "first-exile-cast-cascade-grant-v1", {
            "handler_id": handler.handler_id, "schema_version": handler.schema_version, "event": handler.event,
            "requires_complete_card_program": True, "grant": grant.to_dict(),
        }, (*handler.capability_dependencies, FIRST_EXILE_CASCADE_CAPABILITY, SPELL_ORIGIN_HISTORY_CAPABILITY)
    match = _GRANT.fullmatch(text)
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
