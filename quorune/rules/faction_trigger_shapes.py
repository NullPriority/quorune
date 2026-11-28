"""Keep both faction results bound to the exact event and entry choice."""

from collections.abc import Mapping

from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY, ENTRY_DESIGNATION_HANDLER_ID, ENTRY_FACTION_CAPABILITY,
    ENTRY_MARDU_JESKAI_CAPABILITY, EntryDesignationKind,
)
from .faction_trigger_model import (
    FACTION_CONDITION_FIELD, FACTION_DRAW_CAPABILITY, FACTION_RAD_CAPABILITY, FACTION_RESULT_FIELD,
    FACTION_ATTACK_MULTIPLIER_CAPABILITY, FACTION_UPKEEP_TOKEN_CAPABILITY,
    faction_branch_capabilities, faction_condition, faction_effects, faction_event,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key


def faction_branch_for_program(program):
    condition = thaw_value(program.event_condition)
    effects = tuple(thaw_value(effect) for effect in program.effects)
    from ..fixed_token_production import CREATED_TOKEN_HASTE_FIELD

    for choice in ("Brotherhood", "Enclave", "Jeskai"):
        if (program.event == faction_event(choice) and condition == faction_condition(choice)
                and effects == faction_effects(choice)):
            version_field = CREATED_TOKEN_HASTE_FIELD if choice == "Jeskai" else "schema_version"
            if (any(type(effect.get(version_field)) is not int for effect in effects)
                    or program.active_zone != "battlefield" or program.target_schema is not None
                    or program.cost_schema is not None or program.trigger_limit is not None or program.handlers):
                raise ValueError("Faction triggers require their original untargeted battlefield instructions")
            return choice
    raise ValueError("Faction triggers require the exact source entry choice, occurrence and result")


def faction_attack_multiplier_dependencies():
    return (ENTRY_MARDU_JESKAI_CAPABILITY, FACTION_ATTACK_MULTIPLIER_CAPABILITY,
            "trigger.participation.typed_source_event")


def _mardu_attack_program_dependencies(program):
    from ..ability_fragments import ability_fragment_to_dict
    from ..trigger_participation import TriggerMultiplierPredicate, TriggerMultiplierSpec

    if (FACTION_ATTACK_MULTIPLIER_CAPABILITY not in program.capability_dependencies
            and not contains_nested_key(program.handlers, "required_faction")):
        return ()
    spec = TriggerMultiplierSpec(TriggerMultiplierPredicate.TYPED_SOURCE_EVENT, schema_version=2,
                                 rule="creature_attacks", required_faction="Mardu")
    descriptor = {"handler_id": "ability.static.trigger-participation.v2", "schema_version": 1,
                  "event": "continuous", "fragment": ability_fragment_to_dict(spec)}
    if ((program.event, program.active_zone) != ("continuous", "battlefield")
            or tuple(thaw_value(handler) for handler in program.handlers) != (descriptor,)
            or type(program.handlers[0]["schema_version"]) is not int or program.effects
            or program.event_condition is not None or program.target_schema is not None
            or program.cost_schema is not None or program.trigger_limit is not None):
        raise ValueError("Mardu is its original faction-gated attack participation")
    return faction_attack_multiplier_dependencies()


def faction_trigger_runtime_dependencies(program):
    from .jeskai_temur_faction import jeskai_temur_runtime_dependencies

    if dependencies := jeskai_temur_runtime_dependencies(program):
        return dependencies
    if attack := _mardu_attack_program_dependencies(program):
        return attack
    branch_caps = {FACTION_DRAW_CAPABILITY, FACTION_RAD_CAPABILITY, FACTION_UPKEEP_TOKEN_CAPABILITY}
    if (branch_caps.intersection(program.capability_dependencies)
            or contains_nested_key(program.effects, FACTION_RESULT_FIELD)
            or _mentions_faction_condition(program.event_condition)):
        return faction_branch_capabilities(faction_branch_for_program(program))
    return _faction_entry_program_dependencies(program)


def _faction_entry_program_dependencies(program):
    scopes = {
        EntryDesignationKind.FACTION.value: ENTRY_FACTION_CAPABILITY,
        EntryDesignationKind.MARDU_JESKAI_FACTION.value: ENTRY_MARDU_JESKAI_CAPABILITY,
    }
    choices = [handler.get("designation") for handler in program.handlers if isinstance(handler, Mapping)
               and type(handler.get("designation")) is str and handler["designation"] in scopes]
    if not choices and not set(scopes.values()).intersection(program.capability_dependencies):
        return ()
    if len(choices) != 1:
        raise ValueError("Faction entry requires exactly one typed choice")
    kind = choices[0]
    descriptor = {"handler_id": ENTRY_DESIGNATION_HANDLER_ID, "schema_version": 1,
                  "event": "zone.change", "designation": kind}
    if (tuple(thaw_value(value) for value in program.handlers) != (descriptor,)
            or type(program.handlers[0]["schema_version"]) is not int
            or (program.event, program.active_zone) != ("zone.change", "all")
            or program.effects or program.event_condition is not None or program.target_schema is not None
            or program.cost_schema is not None or program.trigger_limit is not None):
        raise ValueError("Faction entry requires its single intrinsic designation handler")
    return ENTRY_DESIGNATION_CAPABILITY, scopes[kind]


def _mentions_faction_condition(value):
    if isinstance(value, Mapping):
        return value.get("field") == FACTION_CONDITION_FIELD or any(
            _mentions_faction_condition(child) for child in value.values()
        )
    return isinstance(value, (tuple, list)) and any(_mentions_faction_condition(child) for child in value)
