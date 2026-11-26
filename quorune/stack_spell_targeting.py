from __future__ import annotations

"""Closed stack predicates for current announced target subjects."""

from collections.abc import Mapping
from enum import Enum

from .replacement.immutable import thaw_value


class StackSpellTargetPredicate(str, Enum):
    PLAYER = "spell_targets_player_v1"
    CONTROLLER = "spell_targets_controller_v1"
    CREATURE = "spell_targets_creature_v1"
    ENCHANTMENT = "spell_targets_enchantment_v1"
    CONTROLLED_PERMANENT = "spell_targets_controlled_permanent_v1"
    CONTROLLER_OR_CONTROLLED_PERMANENT = "spell_targets_controller_or_controlled_permanent_v1"
    CONTROLLED_CREATURE = "spell_targets_controlled_creature_v1"
    STACK_CONTROLLER_OR_CONTROLLED_CREATURE = "stack_targets_controller_or_controlled_creature_v1"
    CONTROLLED_ARTIFACT_OR_CREATURE = "spell_targets_controlled_artifact_or_creature_v1"
    SOURCE = "spell_targets_current_ability_source_v1"
    INSTANT_OR_AURA_CONTROLLED_PERMANENT = "instant_or_aura_targets_controlled_permanent_v1"

    def target_schema(self) -> dict:
        return {
            "zones": ["stack"],
            "categories": (
                ["spell", "ability"]
                if self is self.STACK_CONTROLLER_OR_CONTROLLED_CREATURE else ["spell"]
            ),
            "source_exclusion": True,
            "count": 1,
            "predicate": self.value,
        }


STACK_SPELL_TARGET_PREDICATES = frozenset(value.value for value in StackSpellTargetPredicate)
CURRENT_TARGET_COUNTER = {"op": "counter_stack_target", "stack": "$target.0"}
CURRENT_TARGET_COUNTER_CAPABILITIES = ("stack.counter.effect", "target.revalidate_resolution")


def stack_spell_target_predicate(schema) -> StackSpellTargetPredicate | None:
    if not isinstance(schema, Mapping):
        return None
    predicate = schema.get("predicate")
    if not isinstance(predicate, str) or predicate not in STACK_SPELL_TARGET_PREDICATES:
        return None
    spec = StackSpellTargetPredicate(predicate)
    if type(schema.get("count")) is not int or thaw_value(schema) != spec.target_schema():
        raise ValueError("A stack target subject requires its exact closed target schema")
    return spec


def contains_stack_spell_target_predicate(value) -> bool:
    if isinstance(value, Mapping):
        predicate = value.get("predicate")
        return (isinstance(predicate, str) and predicate in STACK_SPELL_TARGET_PREDICATES
                or any(contains_stack_spell_target_predicate(child) for child in value.values()))
    if isinstance(value, (list, tuple)):
        return any(contains_stack_spell_target_predicate(child) for child in value)
    return False
