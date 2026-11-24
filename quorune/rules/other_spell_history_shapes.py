"""Typed resolution and cast-event contracts for the other-spell history fact."""

from collections.abc import Mapping

from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT, event_spell_copy_effect
from ..other_spell_history_model import (
    OTHER_SPELL_CAST_MECHANIC, is_other_spell_draw, is_other_spell_self_cast_condition,
    other_spell_self_cast_condition_capabilities, other_spell_was_cast_this_turn, validate_other_spell_draw,
)
from ..public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value


_DAMAGE = {"op": "damage", "source": "$source", "target": "$target.0", "amount": 2}
_DAMAGE_TARGET = {
    "zones": ["player", "battlefield"], "categories": ["player", "permanent"],
    "predicate": "damageable", "count": 1,
}
_CONDITION_MECHANIC = "fixed-resolution-public-condition"


def other_spell_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .capabilities import capability_dependencies_for_node

    if len(effects) != 2 or not isinstance(target_schema, Mapping):
        return ()
    prefix, wrapper = effects
    if (dict(prefix) != _DAMAGE or type(prefix["amount"]) is not int
            or type(target_schema.get("count")) is not int
            or thaw_value(FrozenMap(target_schema)) != _DAMAGE_TARGET):
        return ()
    try:
        children = validate_other_spell_draw(wrapper)
    except (TypeError, ValueError):
        return ()
    prefix_mechanics = set(wrapper["prefix_mechanic_ids"])
    draw_mechanics = set(wrapper["mechanic_ids"])
    if not {_CONDITION_MECHANIC, *prefix_mechanics, *draw_mechanics} <= set(mechanic_ids):
        return ()
    damage = capability_dependencies_for_node(effects=(prefix,), target_schema=target_schema, mechanic_ids=prefix_mechanics)
    draw = capability_dependencies_for_node(effects=children, target_schema=None, mechanic_ids=draw_mechanics)
    if not damage or not draw:
        return ()
    return tuple(sorted({
        PUBLIC_HISTORY_QUERY_CAPABILITY, "resolution.effect.public_condition", "target.revalidate_resolution",
        *damage, *draw,
    }))


def other_spell_draw_runtime_dependencies(program):
    if not any(is_other_spell_draw(effect) for effect in program.effects):
        return ()
    if program.event != "resolve" or program.active_zone != "stack":
        raise ValueError("The other-spell draw requires its fixed stack spell")
    required = other_spell_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("The other-spell draw lacks its complete damage and draw authorities")
    return required


def bind_other_spell_draw(state, item, effect):
    """Use the current stack incarnation after ordinary target revalidation."""
    if not is_other_spell_draw(effect):
        return effect
    validate_other_spell_draw(effect)
    card = state.cards.get(item.card_object_id)
    if (item.kind not in {"spell", "spell_copy"} or card is None or card.zone != "stack"
            or item.context.get("targets_revalidated") is not True):
        raise ValueError("The other-spell draw requires the current revalidated spell")
    result = other_spell_was_cast_this_turn(state, player=item.controller, excluded_incarnation=card.logical_object_id)
    return {**effect, "cast_fact": result}


def other_spell_self_cast_runtime_dependencies(program):
    marked = OTHER_SPELL_CAST_MECHANIC in program.coverage
    matched = is_other_spell_self_cast_condition(program.event_condition)
    if not marked and not matched:
        return ()
    if (not marked or not matched or "intervening_condition" not in program.coverage
            or program.event != "spell.cast" or program.active_zone != "stack"
            or program.target_schema is not None
            or tuple(dict(effect) for effect in program.effects) != (event_spell_copy_effect(retain_targets_only=True),)):
        raise ValueError("The other-spell trigger requires its exact self-cast and retained spell copy")
    return other_spell_self_cast_condition_capabilities(program.event_condition)


def recheck_other_spell_self_cast(state, context, *, controller, source_object_id):
    from .event_spell_copy import decode_event_spell_copy_source

    source = decode_event_spell_copy_source(context.get(EVENT_SPELL_COPY_CONTEXT))
    referenced = source.item()
    if (context.get("event") != "spell.cast" or context.get("stack") != referenced.ref
            or context.get("logical_object_id") != source.logical_object_id
            or type(source_object_id) is not str or referenced.card_object_id != source_object_id):
        raise ValueError("The other-spell condition lost its exact original cast occurrence")
    return other_spell_was_cast_this_turn(
        state, player=controller, excluded_incarnation=source.logical_object_id,
    )
