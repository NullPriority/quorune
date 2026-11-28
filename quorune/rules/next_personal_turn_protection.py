"""A complete controller protection clause lasting to that player's next turn."""

from __future__ import annotations

from collections.abc import Mapping

from ..source_entry_cast_information import (
    SOURCE_ENTRY_CAST_CAPABILITY, SOURCE_ENTRY_CAST_HAND_MECHANIC, SOURCE_ENTRY_CAST_MECHANIC,
    source_entry_cast_condition, source_entry_cast_program_dependencies,
)

NEXT_PERSONAL_TURN_PROTECTION_CAPABILITY = "protection.player.everything_and_life_until_next_turn"
NEXT_PERSONAL_TURN_PROTECTION_MECHANIC = "fixed-next-personal-turn-player-protection"
NEXT_PERSONAL_TURN_PROTECTION_TEMPLATE = "fixed-next-personal-turn-player-protection-v1"

PROTECTION_OPERATION = "protection_from_everything_until_next_turn"
CAST_ENTRY_PROTECTION_VERSION = 3
CAST_ENTRY_PROTECTION_CAPABILITY = "protection.player.everything_until_next_turn"
CAST_ENTRY_PROTECTION_MECHANIC = "fixed-cast-entry-next-turn-player-protection"
CAST_ENTRY_PROTECTION_TEMPLATE = "fixed-cast-entry-next-turn-player-protection-v1"

ACTIVATED_PROTECTION_VERSION = 4
ACTIVATED_PROTECTION_CAPABILITY = "protection.player.activated_everything_until_next_turn"
ACTIVATED_PROTECTION_MECHANIC = "fixed-activated-next-turn-player-protection"
ACTIVATED_PROTECTION_TEMPLATE = "fixed-activated-next-turn-player-protection-v1"


def cast_entry_protection_effect():
    return {
        "op": PROTECTION_OPERATION,
        "schema_version": CAST_ENTRY_PROTECTION_VERSION,
        "player": "$controller",
        "source": "$stack",
    }


def activated_protection_effect():
    return {**cast_entry_protection_effect(), "schema_version": ACTIVATED_PROTECTION_VERSION}


def activated_protection_node_capabilities(*, effects, target_schema, mechanics, active_zone, cost_schema):
    if (ACTIVATED_PROTECTION_MECHANIC not in mechanics or active_zone != "battlefield"
            or target_schema is not None or cost_schema is None or len(effects) != 1):
        return ()
    effect = effects[0]
    if effect != activated_protection_effect() or type(effect["schema_version"]) is not int:
        return ()
    return (ACTIVATED_PROTECTION_CAPABILITY,)


def _activated_protection_program_dependencies(program):
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    required = activated_protection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    if (not required or program.event != "activate" or program.event_condition is not None
            or len(program.handlers) != 1):
        raise ValueError("Next-turn protection requires a complete untargeted battlefield activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if ability.mana_ability or ability.builtin_semantic_key != program.key:
        raise ValueError("Next-turn protection requires the exact nonmana activation")
    return required


def validate_resolving_activated_protection(host, effect, *, actor):
    source = effect.get("source")
    expected = {**activated_protection_effect(), "player": actor, "source": source}
    instruction = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
    if (type(source) is not str or not source or type(effect.get("schema_version")) is not int
            or instruction != expected):
        raise ValueError("Next-turn protection requires the resolving controller and stack reference")
    item = next((item for item in host.state.stack if item.ref == source and item.controller == actor
                 and item.kind in {"activated_ability", "activated_ability_copy"}
                 and item.context.get("currently_resolving") is True), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ValueError("Next-turn protection requires its admitted resolving activation")
    _activated_protection_program_dependencies(program)


def next_personal_turn_protection_effect():
    return {
        "op": PROTECTION_OPERATION,
        "schema_version": 2,
        "player": "$controller",
        "source": "$stack",
        "preserve_life_total": True,
    }


def next_personal_turn_protection_dependencies(value):
    if not isinstance(value, Mapping) or value.get("op") != PROTECTION_OPERATION:
        return ()
    if "schema_version" not in value and "preserve_life_total" not in value:
        return ()
    from .targeted_opponent_protection import CAPABILITY, protection_effect

    if type(value.get("schema_version")) is int:
        if value == protection_effect() and value["preserve_life_total"] is True:
            return (CAPABILITY,)
        if value == activated_protection_effect():
            return (ACTIVATED_PROTECTION_CAPABILITY,)
        if value == cast_entry_protection_effect():
            return (CAST_ENTRY_PROTECTION_CAPABILITY,)
        if value == next_personal_turn_protection_effect() and value["preserve_life_total"] is True:
            return (NEXT_PERSONAL_TURN_PROTECTION_CAPABILITY,)
    raise ValueError("Next-turn protection requires an exact controller instruction")


def cast_entry_protection_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or target_schema is not None
            or not {CAST_ENTRY_PROTECTION_MECHANIC, SOURCE_ENTRY_CAST_MECHANIC}.issubset(mechanic_ids)
            or SOURCE_ENTRY_CAST_HAND_MECHANIC in mechanic_ids):
        return ()
    try:
        required = next_personal_turn_protection_dependencies(effects[0])
    except ValueError:
        return ()
    if required != (CAST_ENTRY_PROTECTION_CAPABILITY,):
        return ()
    return (*required, SOURCE_ENTRY_CAST_CAPABILITY)


def _cast_entry_protection_program_dependencies(program):
    required = cast_entry_protection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.handlers or program.cost_schema is not None
            or program.event_condition != source_entry_cast_condition()
            or not source_entry_cast_program_dependencies(program)):
        raise ValueError("Cast-entry protection requires the original untargeted cast-conditioned self entry")
    return required


def validate_resolving_cast_entry_protection(host, effect, *, actor):
    from ..continuous_effect_model import ContinuousObjectIdentity
    from .entry_cast_information import source_entry_cast_value

    source = effect.get("source")
    expected = {**cast_entry_protection_effect(), "player": actor, "source": source}
    instruction = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
    if (type(source) is not str or not source or type(effect.get("schema_version")) is not int
            or instruction != expected):
        raise ValueError("Cast-entry protection requires the resolving controller and its sealed stack reference")
    item = next((item for item in host.state.stack if item.ref == source and item.controller == actor
                 and item.kind == "triggered_ability" and item.context.get("currently_resolving") is True), None)
    if item is None or not source_entry_cast_value(
        host, item.context, controller=actor,
        source=ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id")),
    ):
        raise ValueError("Cast-entry protection requires the original cast-conditioned trigger")


def next_personal_turn_protection_effects():
    from ..phasing_model import controlled_permanent_set_phase_out_effect

    return (next_personal_turn_protection_effect(), controlled_permanent_set_phase_out_effect())


def next_personal_turn_protection_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..phasing_model import (
        CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY,
        PHASE_OUT_CAPABILITY,
        PHASING_CAPABILITY,
        validate_phase_out_effect,
    )

    if (NEXT_PERSONAL_TURN_PROTECTION_MECHANIC not in mechanic_ids or target_schema is not None
            or tuple(effects) != next_personal_turn_protection_effects()):
        return ()
    try:
        next_personal_turn_protection_dependencies(effects[0])
        validate_phase_out_effect(effects[1])
    except ValueError:
        return ()
    return (
        NEXT_PERSONAL_TURN_PROTECTION_CAPABILITY, "life.change.effect",
        CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY, PHASE_OUT_CAPABILITY, PHASING_CAPABILITY,
    )


def next_personal_turn_protection_runtime_dependencies(program):
    from ..phasing_model import CONTROLLED_PERMANENT_PHASE_OUT_SUBJECT, PHASE_OUT_OPERATION
    from .effect_shape_walkers import nested_effects
    from .gift_extra_turn_spells import gift_extra_turn_runtime_dependencies
    from .targeted_opponent_protection import MECHANIC, program_dependencies, uses_targeted_opponent_protection

    if required := gift_extra_turn_runtime_dependencies(program):
        return required
    effects = tuple(nested_effects((*program.effects, *program.handlers)))
    if MECHANIC in program.coverage or any(map(uses_targeted_opponent_protection, effects)):
        return program_dependencies(program)
    if ACTIVATED_PROTECTION_MECHANIC in program.coverage or any(
        effect.get("op") == PROTECTION_OPERATION and effect.get("schema_version") == ACTIVATED_PROTECTION_VERSION
        for effect in effects
    ):
        return _activated_protection_program_dependencies(program)
    if CAST_ENTRY_PROTECTION_MECHANIC in program.coverage or any(
        effect.get("op") == PROTECTION_OPERATION and effect.get("schema_version") == CAST_ENTRY_PROTECTION_VERSION
        for effect in effects
    ):
        return _cast_entry_protection_program_dependencies(program)
    if not any(
        (effect.get("op") == PROTECTION_OPERATION
         and ("schema_version" in effect or "preserve_life_total" in effect))
        or (effect.get("op") == PHASE_OUT_OPERATION
            and (effect.get("subject") == CONTROLLED_PERMANENT_PHASE_OUT_SUBJECT or effect.get("schema_version") == 5))
        for effect in effects
    ):
        return ()
    required = next_personal_turn_protection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.handlers
            or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Next-turn life and protection require the complete untargeted resolving spell")
    return required
