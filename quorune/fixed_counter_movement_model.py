"""Printed counter movement roles that reuse the existing atomic movement owner."""

from collections.abc import Mapping
from dataclasses import dataclass


FIXED_COUNTER_MOVEMENT_OPERATION = "move_fixed_counter"
FIXED_COUNTER_MOVEMENT_CAPABILITY = "counter.producer.fixed_movement"
CHOSEN_COUNTER_MOVEMENT_CAPABILITY = "counter.producer.chosen_movement"
OPTIONAL_COMBAT_COUNTER_MOVEMENT_CAPABILITY = "counter.producer.optional_chosen_combat_movement"
OPTIONAL_TARGET_TO_SOURCE = "optional_target_to_source"
CONTROLLED_TARGET_TO_SOURCE = "controlled_target_to_source"
TWO_DISTINCT_TARGETS = "two_distinct_targets"
CHOSEN_PERMANENT_TARGETS = "chosen_permanent_targets"
CHOSEN_CONTROLLED_PERMANENT_QUANTITIES = "chosen_controlled_permanent_quantities"
CHOSEN_COUNTER_QUANTITIES_CAPABILITY = "counter.producer.chosen_movement_quantities"
CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED = "chosen_opponent_creature_to_controlled"
CHOSEN_OPPONENT_COUNTER_MOVEMENT_CAPABILITY = "counter.producer.chosen_opponent_creature_movement"
OPTIONAL_CHOSEN_CONTROLLED_CREATURES = "optional_chosen_controlled_creatures"
_FIELDS = {"op", "schema_version", "form", "player", "from_ref", "to_ref"}


@dataclass(frozen=True, slots=True)
class FixedCounterMovementSpec:
    form: str
    schema_version: int = 1

    def __post_init__(self):
        valid_forms = {
            1: (OPTIONAL_TARGET_TO_SOURCE, CONTROLLED_TARGET_TO_SOURCE, TWO_DISTINCT_TARGETS),
            2: (CHOSEN_PERMANENT_TARGETS,),
            3: (OPTIONAL_CHOSEN_CONTROLLED_CREATURES,),
            4: (CHOSEN_CONTROLLED_PERMANENT_QUANTITIES,),
            5: (CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED,),
        }
        if type(self.schema_version) is not int or self.form not in valid_forms.get(self.schema_version, ()):
            raise ValueError("Unsupported fixed counter movement")

    @property
    def optional(self):
        return self.form in (OPTIONAL_TARGET_TO_SOURCE, OPTIONAL_CHOSEN_CONTROLLED_CREATURES)

    @property
    def chooses_counter_kind(self):
        return self.form in (CHOSEN_PERMANENT_TARGETS, OPTIONAL_CHOSEN_CONTROLLED_CREATURES,
                             CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED)

    @property
    def chooses_counter_quantities(self):
        return self.form == CHOSEN_CONTROLLED_PERMANENT_QUANTITIES

    @property
    def receiver_is_ability_source(self):
        return self.form != TWO_DISTINCT_TARGETS and not (
            self.chooses_counter_kind or self.chooses_counter_quantities)

    def effect(self):
        return {
            "op": FIXED_COUNTER_MOVEMENT_OPERATION, "schema_version": self.schema_version, "form": self.form,
            "player": "$controller", "from_ref": "$target.0",
            "to_ref": "$source" if self.receiver_is_ability_source else "$target.1",
        }

    @classmethod
    def from_effect(cls, value, *, bound=False):
        if (not isinstance(value, Mapping) or set(value) != _FIELDS
                or value["op"] != FIXED_COUNTER_MOVEMENT_OPERATION):
            raise ValueError("Fixed counter movement requires both exact roles")
        spec = cls(value["form"], value["schema_version"])
        if bound:
            player = value["player"]
            cards = (value["from_ref"], value["to_ref"])
            if (type(player) is not str or not player or player.startswith("$")
                    or any(card is not None and (type(card) is not str or not card or card.startswith("$"))
                           for card in cards)):
                raise ValueError("Fixed counter movement requires resolved player and object references")
        elif dict(value) != spec.effect():
            raise ValueError("Fixed counter movement lost its original printed roles")
        return spec
