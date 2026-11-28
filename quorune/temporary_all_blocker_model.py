from __future__ import annotations

"""Closed resolved all-able-blocker rule, distinct from an object ability."""

from dataclasses import dataclass
from typing import Mapping

from .declaration_fragments import DeclarationRequirementTemplate
from .replacement.immutable import thaw_value


TEMPORARY_ALL_BLOCKER_KIND = 'all_able_blockers'
TEMPORARY_ALL_BLOCKER_CAPABILITY = 'combat.requirement.resolved_all_blockers'
BLOCKER_PROTECTION_CONDITION_VERSION = 41


@dataclass(frozen=True, slots=True)
class TemporaryAllBlockerSpec:
    schema_version: int = 1
    kind: str = TEMPORARY_ALL_BLOCKER_KIND

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or self.kind != TEMPORARY_ALL_BLOCKER_KIND:
            raise ValueError('Temporary all-blocker requirements have one closed version and kind')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'kind': self.kind}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind'}:
            raise ValueError('Temporary all-blocker requirement fields are closed')
        return cls(**value)

    def to_requirement(self):
        return DeclarationRequirementTemplate('intrinsic-all-able-blockers-v1', 'block', 'all_able_blockers')


BLOCKER_PROTECTION_MECHANICS = ("cr-611-continuous-effects", "cr-115-targets", "indestructible")
TARGET_BLOCKER_MECHANICS = ("cr-611-continuous-effects", "cr-509-declare-blockers-step", "cr-115-targets")


def validate_blocker_protection_condition(effect):
    from .continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
    from .object_predicate import ObjectQuerySpec
    from .resolution_conditions import RESOLUTION_CONDITION_OPERATION

    fields = {"op", "schema_version", "player", "condition", "effects", "mechanic_ids", "prefix_mechanic_ids"}
    if (
        set(effect) != fields or effect["op"] != RESOLUTION_CONDITION_OPERATION
        or type(effect["schema_version"]) is not int or effect["schema_version"] != BLOCKER_PROTECTION_CONDITION_VERSION
        or type(effect["player"]) is not str or not effect["player"]
        or tuple(effect["mechanic_ids"]) != BLOCKER_PROTECTION_MECHANICS
        or tuple(effect["prefix_mechanic_ids"]) != TARGET_BLOCKER_MECHANICS
        or not isinstance(effect["effects"], (tuple, list)) or len(effect["effects"]) != 1
    ):
        raise ValueError("Blocking protection requires the complete ordered public condition")
    condition = FixedPublicStateConditionSpec.from_dict(effect["condition"])
    predicate = condition.predicate
    numeric = predicate.numeric_characteristic if predicate is not None else None
    if (
        condition.kind is not FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS
        or numeric is None or numeric.characteristic.value != "power"
        or numeric.comparison.value != "at_least" or numeric.value != 4
        or predicate != ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), numeric_characteristic=numeric)
    ):
        raise ValueError("Blocking protection requires the controller's current four-power creature")
    child, = effect["effects"]
    card = child.get("card") if isinstance(child, Mapping) else None
    if (
        type(card) is not str or not card
        or (card != "$target.0" if effect["player"] == "$controller" else card.startswith("$"))
        or thaw_value(child) != {"op": "grant_keyword_until_end_of_turn", "card": card, "keyword": "Indestructible"}
    ):
        raise ValueError("Blocking protection requires its single announced creature")
    return condition, (child,)
