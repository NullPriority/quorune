from __future__ import annotations

"""Closed fixed mana results; output selection is owned separately."""

from dataclasses import dataclass
from .counter_names import normalized_counter_name

FIXED_MANA_RESULT_CAPABILITY = 'mana.activated.fixed_result'
FIXED_MANA_LIFE_GAIN_CAPABILITY = "mana.activated.fixed_life_gain"
COUNTER_DEPLETION_RESULT_CAPABILITY = 'mana.activated.counter_depletion_result'
GAIN_CONTROLLER_LIFE = "gain_controller_life"
PLACE_MANA_POINT_COUNTER = "place_mana_point_counter"
FIXED_MANA_POINT_COUNTER_CAPABILITY = "mana.activated.fixed_point_counter"
EXERT_MANA_SOURCE = "exert_mana_source"
EXERT_MANA_CAPABILITY = "mana.activated.exert_source"
DISCARD_HAND_MANA_SOURCE = "whole_hand_mana_cost"
WHOLE_HAND_MANA_CAPABILITY = "mana.activated.whole_hand_sacrifice"
DOUBLE_UNSPENT_MANA = "double_unspent_mana"
DOUBLE_UNSPENT_MANA_CAPABILITY = "mana.activated.double_unspent_mana"
_DOUBLE_UNSPENT_TEXT = "double the amount of each type of unspent mana you have."


def double_unspent_mana_text(cost_text, effect_text):
    return (" ".join(cost_text.split()).casefold() == "{3}, {t}"
            and " ".join(effect_text.split()).casefold() == _DOUBLE_UNSPENT_TEXT)


def doubles_unspent_mana(ability):
    result = ability.fixed_mana_result
    return result is not None and result.kind == DOUBLE_UNSPENT_MANA



@dataclass(frozen=True, slots=True)
class FixedManaResultSpec:
    kind: str
    amount: int = 0
    schema_version: int = 1
    counter_name: str | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4, 5, 6, 7}:
            raise ValueError('Fixed mana result schema version is unsupported')
        if self.schema_version == 7:
            if self.kind != DOUBLE_UNSPENT_MANA or type(self.amount) is not int or self.amount or self.counter_name is not None:
                raise ValueError("Mana doubling requires its exact production marker")
            return
        if self.schema_version == 6:
            if self.kind != DISCARD_HAND_MANA_SOURCE or type(self.amount) is not int or self.amount or self.counter_name is not None:
                raise ValueError("Whole-hand mana cost requires its exact marker")
            return
        if self.schema_version == 5:
            if self.kind != EXERT_MANA_SOURCE or type(self.amount) is not int or self.amount or self.counter_name is not None:
                raise ValueError("Fixed exert mana requires one unmodified self-exert cost")
            return
        if self.schema_version == 4:
            if (self.kind != PLACE_MANA_POINT_COUNTER or type(self.amount) is not int
                    or self.amount != 1 or self.counter_name != "point"):
                raise ValueError("Mana point placement requires exactly one point counter")
            return
        if self.schema_version == 3:
            if (self.kind != GAIN_CONTROLLER_LIFE or type(self.amount) is not int
                    or not 1 <= self.amount <= 20 or self.counter_name is not None):
                raise ValueError("Mana life gain requires one fixed positive amount")
            return
        if self.schema_version == 2:
            if (self.kind != 'sacrifice_if_no_counters' or type(self.amount) is not int or self.amount != 0
                    or type(self.counter_name) is not str or not self.counter_name
                    or normalized_counter_name(self.counter_name) != self.counter_name):
                raise ValueError('Counter-depletion mana results require one exact named counter')
            return
        if self.counter_name is not None:
            raise ValueError('Historical fixed mana results cannot declare a counter name')
        if type(self.amount) is not int or type(self.kind) is not str or self.kind not in {'damage_controller','skip_actor_untap'}:
            raise ValueError('Fixed mana result requires a closed kind and integer amount')
        if (self.kind == 'damage_controller' and self.amount <= 0
                or self.kind == 'skip_actor_untap' and self.amount != 0):
            raise ValueError('Fixed mana result amount does not match its kind')

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        if self.schema_version == 7:
            return (FIXED_MANA_RESULT_CAPABILITY, DOUBLE_UNSPENT_MANA_CAPABILITY)
        if self.schema_version == 6:
            return (WHOLE_HAND_MANA_CAPABILITY, "zone.discard.whole_hand", "zone.change.destination_replacement")
        if self.schema_version == 5:
            return (EXERT_MANA_CAPABILITY, "untap.step.exert_player")
        if self.schema_version == 4:
            return (FIXED_MANA_RESULT_CAPABILITY, FIXED_MANA_POINT_COUNTER_CAPABILITY)
        if self.schema_version == 3:
            return (FIXED_MANA_RESULT_CAPABILITY, FIXED_MANA_LIFE_GAIN_CAPABILITY)
        if self.schema_version == 2:
            return (FIXED_MANA_RESULT_CAPABILITY, COUNTER_DEPLETION_RESULT_CAPABILITY)
        if self.kind == "skip_actor_untap":
            return (FIXED_MANA_RESULT_CAPABILITY, "untap.step.exert_player")
        return (FIXED_MANA_RESULT_CAPABILITY,)

    def to_dict(self):
        return {'schema_version':self.schema_version,'kind':self.kind,'amount':self.amount,
                **({'counter_name':self.counter_name} if self.schema_version in {2, 4} else {})}

    @classmethod
    def from_dict(cls,value):
        expected = {'schema_version','kind','amount'}
        if isinstance(value,dict) and value.get('schema_version') in {2, 4}:expected.add('counter_name')
        if not isinstance(value,dict) or set(value) != expected:
            raise ValueError('Fixed mana result fields are closed')
        return cls(**value)

    def mode_effect(self):
        if self.schema_version == 7:
            raise ValueError("Doubling is a postpayment mana output")
        if self.schema_version == 6:
            raise ValueError("Whole-hand mana costs are committed before producing mana")
        if self.schema_version == 5:
            return {"op": EXERT_MANA_SOURCE}
        if self.schema_version == 4:
            return {"op": PLACE_MANA_POINT_COUNTER}
        if self.schema_version == 3:
            return {"op": GAIN_CONTROLLER_LIFE, "amount": self.amount}
        if self.schema_version == 2:
            return {'op':'sacrifice_if_no_counters','counter_name':self.counter_name}
        return ({'op':'damage_self','amount':self.amount} if self.kind == 'damage_controller'
                else {'op':'skip_actor_untap'})
