"""Validate and release the closed Gift spell instructions."""

from collections.abc import Mapping

from ..gift_card_model import (
    GIFT_CARD_CAPABILITY, GIFT_CARD_COST_FIELD, GIFT_CARD_PRICE, GIFT_CARD_PROMISE_FIELD,
    GIFT_CARD_TEMPLATE, GIFT_EXTRA_TURN_COST_FIELD, GiftCardPromise, bind_gift_promise_target_schema,
)
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .all_player_turn_rules import controller_turn_hexproof_instruction
from .gift_counter_spells import gift_counter_effect, gift_counter_runtime_dependencies, gift_counter_target_schema


def gift_protection_effects():
    return (
        controller_turn_hexproof_instruction(),
        {
            "op": "modify_all_matching_permanents_until_end_of_turn",
            "predicate": ObjectQuerySpec(zones=("battlefield",), controller="$controller").to_dict(),
            "power": 0,
            "toughness": 0,
            "keywords": ["Hexproof"],
        },
    )


def gift_card_runtime_dependencies(program):
    from .gift_extra_turn_spells import gift_extra_turn_runtime_dependencies

    if required := gift_extra_turn_runtime_dependencies(program):
        return required
    cost = program.cost_schema
    provenance = program.provenance or {}
    if not (isinstance(cost, Mapping) and GIFT_CARD_COST_FIELD in cost
            or provenance.get("template_id") == GIFT_CARD_TEMPLATE):
        return ()
    price = cost.get(GIFT_CARD_COST_FIELD) if isinstance(cost, Mapping) else None
    if (
        set(cost or {}) != {GIFT_CARD_COST_FIELD}
        or not isinstance(price, Mapping)
        or dict(price) != GIFT_CARD_PRICE
        or type(price["schema_version"]) is not int
        or (program.event, program.active_zone) != ("resolve", "stack")
        or program.event_condition is not None or program.target_schema is not None or program.handlers
        or tuple(thaw_value(program.effects)) != gift_protection_effects()
    ):
        raise ValueError("Gift a card requires its exact untargeted spell and protection result")
    return (GIFT_CARD_CAPABILITY,)


def is_closed_gift_card_spell(program):
    try:
        required = gift_card_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def bind_gift_stack_schema(program, item, schema):
    from .gift_fish_spells import bind_gift_fish_stack_schema

    from . import gift_graveyard_spells as graveyard

    if graveyard.runtime_dependencies(program):
        return bind_gift_promise_target_schema(item, unpromised=graveyard.target_schema_for_program(program, False),
                                               promised=graveyard.target_schema_for_program(program, True))
    if gift_counter_runtime_dependencies(program):
        return bind_gift_promise_target_schema(item, unpromised=gift_counter_target_schema(False),
                                               promised=gift_counter_target_schema(True))
    return bind_gift_fish_stack_schema(program, item, schema)


def _gift_card_draw(host, recipient):
    if recipient is None:
        return []
    if recipient not in host.state.players:
        raise ValueError("Gift a card resolution lost its chosen player")
    return ([{"op": "draw", "player": recipient, "count": 1, "private": True}]
            if recipient in host.active_seats else [])


def prepare_gift_card_resolution(host, item, effects):
    program = host.semantics.get(item.semantic_key)
    if program is None:
        return effects
    from .gift_fish_spells import gift_fish_runtime_dependencies, prepare_gift_fish_resolution

    if gift_fish_runtime_dependencies(program):
        return prepare_gift_fish_resolution(host, item, effects, program)
    from . import gift_graveyard_spells as graveyard

    if graveyard.runtime_dependencies(program):
        if item.kind not in {"spell", "spell_copy"} or tuple(effects) != graveyard.gift_graveyard_resolution(program, False):
            raise ValueError("Gift reanimation lost its original return instruction")
        promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
        if graveyard.SWEEP_RETURN_COST_FIELD in program.cost_schema:
            return [*_gift_card_draw(host, promise.recipient),
                    graveyard.prepare_gift_sweep_resolution(item, promise.recipient is not None)]
        return [*_gift_card_draw(host, promise.recipient),
                *graveyard.gift_graveyard_resolution(program, promise.recipient is not None)]
    if gift_counter_runtime_dependencies(program):
        if item.kind not in {"spell", "spell_copy"} or tuple(effects) != (gift_counter_effect(),):
            raise ValueError("Gift a card resolution lost its original counter instruction")
        promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
        return [*_gift_card_draw(host, promise.recipient), *effects]
    if not gift_card_runtime_dependencies(program):
        return effects
    if GIFT_EXTRA_TURN_COST_FIELD in program.cost_schema:
        from .gift_extra_turn_spells import prepare_gift_extra_turn_resolution

        return prepare_gift_extra_turn_resolution(host, item, effects)
    if item.kind not in {"spell", "spell_copy"} or tuple(effects) != gift_protection_effects():
        raise ValueError("Gift a card resolution lost its validated spell instructions")
    promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
    if promise.recipient is None:
        return effects
    player_protection, permanent_protection = effects
    permanent_protection = {**permanent_protection, "keywords": ["Hexproof", "Indestructible"]}
    return [*_gift_card_draw(host, promise.recipient), player_protection, permanent_protection]
