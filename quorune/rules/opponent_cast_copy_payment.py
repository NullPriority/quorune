"""The optional cast-copy result of declining an opponent's mana payment."""

from __future__ import annotations

from collections.abc import Mapping

from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY, EVENT_SPELL_COPY_CONTEXT, event_spell_copy_effect
from ..replacement.immutable import FrozenMap
from .event_spell_copy import decode_event_spell_copy_source


OPPONENT_CAST_COPY_PAYMENT_CAPABILITY = "effect.choice.opponent_cast_payment_decline_copy"
_RESULT_DEPENDENCIES = (OPPONENT_CAST_COPY_PAYMENT_CAPABILITY, EVENT_SPELL_COPY_CAPABILITY, "effect.choice.optional_fixed")


def opponent_instant_or_sorcery_condition():
    return {"all": [
        {"field": "controller", "op": "ne", "value": "$source.controller"},
        {"field": "types", "op": "contains_any", "value": ["instant", "sorcery"]},
    ]}


def optional_cast_copy_result():
    return {"op": "offer_optional_effect", "player": "$controller", "effects": [event_spell_copy_effect()]}


def is_optional_cast_copy_result(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "offer_optional_effect"


def validate_optional_cast_copy_result(effect, *, beneficiary, resolved=False):
    children = effect.get("effects")
    if (set(effect) != {"op", "player", "effects"} or not is_optional_cast_copy_result(effect)
            or effect["player"] != beneficiary or not isinstance(children, (tuple, list)) or len(children) != 1
            or not isinstance(children[0], Mapping)):
        raise ValueError("Declined opponent payment requires the beneficiary's separate optional spell copy")
    child, = children
    if not resolved:
        if FrozenMap(child) != FrozenMap(event_spell_copy_effect()):
            raise ValueError("The optional copy must reference the exact cast occurrence")
        return _RESULT_DEPENDENCIES
    if set(child) != {"op", "stack", "copy_source"} or child["op"] != "copy_stack_item":
        raise ValueError("The declined payment lost its original optional spell copy")
    original = decode_event_spell_copy_source(child["copy_source"])
    if child["stack"] != original.item().ref:
        raise ValueError("The optional copy must retain the original stack spell")
    return _RESULT_DEPENDENCIES


def validate_cast_copy_payment_occurrence(item, child, payer):
    captured = decode_event_spell_copy_source(item.context.get(EVENT_SPELL_COPY_CONTEXT))
    bound, = child["effects"]
    original = decode_event_spell_copy_source(bound["copy_source"])
    if (captured.logical_object_id != original.logical_object_id or captured.item().ref != original.item().ref
            or captured.item().kind != "spell"
            or item.controller == payer or item.context.get("stack") != bound["stack"]
            or not {"instant", "sorcery"}.intersection(item.context.get("types", ()))):
        raise ValueError("The original opposing instant or sorcery and its payer must remain bound")
