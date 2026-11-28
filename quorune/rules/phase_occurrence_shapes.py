from __future__ import annotations

"""Capability closure for an exact spell-target phase-out or phase-in token result."""

from collections.abc import Mapping

from .phase_occurrence_model import (
    PHASE_IN_CAPABILITY, PHASE_IN_EVENT, PHASE_IN_MECHANIC, SPELL_TARGET_PHASE_OUT_CAPABILITY, observed_subtype,
)
from ..phasing_model import PHASE_OUT_CAPABILITY, PHASING_CAPABILITY, spell_target_phase_out_effect
from ..replacement.immutable import FrozenMap, thaw_value
from .target_announcements import TARGET_ANNOUNCEMENT_CAPABILITY, TARGET_ANNOUNCEMENT_EVENT
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities, token_creation_component_mechanics


_TRIGGER_DEPENDENCIES = ("trigger.effect.fixed_event", "trigger.placement.apnap", PHASING_CAPABILITY)
SPELL_TARGET_PHASE_OUT_DEPENDENCIES = (
    SPELL_TARGET_PHASE_OUT_CAPABILITY, TARGET_ANNOUNCEMENT_CAPABILITY, PHASE_OUT_CAPABILITY, *_TRIGGER_DEPENDENCIES,
)


def spell_target_phase_out_runtime_dependencies(program):
    if ((program.event, program.active_zone) != (TARGET_ANNOUNCEMENT_EVENT, "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(spell_target_phase_out_effect())
            or observed_subtype(program.event_condition, spell_target=True) is None):
        raise ValueError("Occurrence phase-out requires its original complete spell-target trigger")
    return SPELL_TARGET_PHASE_OUT_DEPENDENCIES


def phase_in_token_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    effect = effects[0]
    definition = effect.get("characteristics")
    if (effect.get("op") != "create_token" or effect.get("controller") != "$controller"
            or type(effect.get("quantity")) is not int or effect["quantity"] != 1 or effect.get("tapped") is not True
            or not isinstance(definition, Mapping)
            or FrozenMap({key: definition.get(key) for key in ("power", "toughness", "colors", "keywords")})
            != FrozenMap({"power": "1", "toughness": "1", "colors": ["W"], "keywords": ["Flying"]})):
        return ()
    canonical = (thaw_value(FrozenMap(effect)),)
    mechanics = token_creation_component_mechanics(canonical, mechanic_ids)
    if not mechanics <= set(mechanic_ids):
        return ()
    owned = fixed_token_creation_node_capabilities(effects=canonical, target_schema=None, mechanic_ids=mechanics)
    return (PHASE_IN_CAPABILITY, *_TRIGGER_DEPENDENCIES, "combat.block.flying", *owned) if owned else ()


def phase_in_token_runtime_dependencies(program):
    if program.event != PHASE_IN_EVENT and PHASE_IN_MECHANIC not in program.coverage:
        return ()
    subtype = observed_subtype(program.event_condition, spell_target=False)
    owned = phase_in_token_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if ((program.event, program.active_zone) != (PHASE_IN_EVENT, "battlefield")
            or program.cost_schema is not None or program.handlers or not owned or subtype is None
            or program.effects[0]["characteristics"].get("type_line", "").casefold() != f"token creature — {subtype}"):
        raise ValueError("A phase-in token requires its complete battlefield occurrence and matching tapped token")
    return owned
