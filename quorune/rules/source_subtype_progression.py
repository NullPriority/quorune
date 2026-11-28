"""Closed subtype-conditioned self changes and their granted Ring damage result."""

from __future__ import annotations

from collections.abc import Mapping

from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_from_dict, ability_fragment_to_dict
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..replacement.immutable import thaw_value
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION
from ..ring_model import RING_TEMPTATION_CAPABILITY
from .event_player_targets import is_source_combat_damage_player_binding


PROGRESSION_FIELD = "source_subtype_progression_version"
PROGRESSION_CAPABILITY = "continuous.resolution.source_subtype_progression"
RING_DAMAGE_OUTCOME_FIELD = "ring_combat_player_outcome_version"
RING_DAMAGE_OUTCOME_CAPABILITY = "resolution.ring.combat_player_outcome"
RING_DAMAGE_OUTCOME_TEMPLATE = "ring-combat-player-loss-or-tempt-v1"
DAMAGED_PLAYER_LOSS_CAPABILITY = "game.loss.effect"


def progression_effect(stage, granted=None):
    effect = {"op": CHARACTERISTIC_OPERATION, PROGRESSION_FIELD: 1, "stage": stage, "card": "$source.zone_object"}
    if granted is not None:
        effect["ability_fragments"] = [dict(granted)]
    return effect


def progression_parts(effect, *, materialized=False):
    stage = effect.get("stage")
    raw_card = effect.get("card")
    fields = {"op", PROGRESSION_FIELD, "stage", "card"}
    if stage == "rogue":
        fields.add("ability_fragments")
    if (
        stage not in {"scout", "rogue"} or set(effect) != fields
        or effect["op"] != CHARACTERISTIC_OPERATION
        or type(effect[PROGRESSION_FIELD]) is not int or effect[PROGRESSION_FIELD] != 1
        or (raw_card is not None and (type(raw_card) is not str or not raw_card or raw_card.startswith("$"))
            if materialized else raw_card != "$source.zone_object")
    ):
        raise ValueError("Subtype progression requires its original resolving source and closed stage")
    fragment = None
    if stage == "rogue":
        fragments = effect["ability_fragments"]
        if not isinstance(fragments, (list, tuple)) or len(fragments) != 1:
            raise ValueError("The Rogue stage requires one ability fragment")
        fragment = ability_fragment_from_dict(fragments[0])
        if (not isinstance(fragment, GrantedTriggeredAbilitySpec) or fragment.event != "damage.dealt.self"
                or thaw_value(fragments[0]) != ability_fragment_to_dict(fragment)):
            raise ValueError("The Rogue stage requires its independently bound combat-damage grant")
    return stage, fragment


def progression_dependencies(effect):
    stage, _fragment = progression_parts(effect)
    return (
        PROGRESSION_CAPABILITY, "continuous.resolution.fixed_source_characteristics_until_end_of_turn",
        *((*FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Lifelink"],) if stage == "scout" else (RING_DAMAGE_OUTCOME_CAPABILITY,)),
    )


def progression_program_dependencies(program):
    if not any(PROGRESSION_FIELD in row for row in program.effects):
        return ()
    if (program.event != "activate" or program.active_zone != "battlefield" or program.target_schema is not None
            or any(handler.get("handler_id") != "activation.catalog.pinned.v1" for handler in program.handlers)
            or len(program.effects) != 1):
        raise ValueError("Subtype progression requires its complete untargeted activation")
    return progression_dependencies(program.effects[0])


def ring_damage_outcome_effect():
    return {"op": "apply_if_public_condition", RING_DAMAGE_OUTCOME_FIELD: 1,
            "player": "$controller", "damaged_player": "$context.target"}


def uses_ring_damage_outcome(effect):
    return isinstance(effect, Mapping) and RING_DAMAGE_OUTCOME_FIELD in effect


def ring_damage_outcome_dependencies(effect):
    if (not uses_ring_damage_outcome(effect) or type(effect[RING_DAMAGE_OUTCOME_FIELD]) is not int
            or thaw_value(effect) != ring_damage_outcome_effect()):
        raise ValueError("The Ring damage branch requires its sealed controller and damaged player")
    return (RING_DAMAGE_OUTCOME_CAPABILITY, "resolution.effect.public_condition",
            RING_TEMPTATION_CAPABILITY, DAMAGED_PLAYER_LOSS_CAPABILITY,
            "trigger.event.normalized_damage", "trigger.effect.fixed_event", "trigger.placement.apnap")


def ring_damage_trigger_node_dependencies(effects):
    if len(effects) != 1 or not uses_ring_damage_outcome(effects[0]):
        return ()
    return ring_damage_outcome_dependencies(effects[0])


def ring_damage_outcome_program_dependencies(program):
    if not any(uses_ring_damage_outcome(effect) for effect in program.effects):
        return ()
    if (
        program.active_zone != "battlefield" or program.target_schema is not None or program.cost_schema is not None
        or program.handlers or len(program.effects) != 1
        or not is_source_combat_damage_player_binding(program.event, program.event_condition)
    ):
        raise ValueError("The Ring damage branch requires one untargeted combat damage occurrence against a player")
    return ring_damage_outcome_dependencies(program.effects[0])
