from __future__ import annotations

"""Immutable fixed alternative mana costs granted by a battlefield source."""

from dataclasses import dataclass
from enum import Enum
import math
from typing import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value

GLOBAL_ALTERNATIVE_CAPABILITY='casting.alternative_cost.fixed_battlefield_grant'
GLOBAL_ALTERNATIVE_HANDLER='casting.alternative-cost.fixed-battlefield-grant.v1'
GLOBAL_ALTERNATIVE_EVENT='cast.cost'
TURN_LIMITED_ALTERNATIVE_CAPABILITY='casting.alternative_cost.turn_limited_battlefield_grant'
MANA_KEYS=frozenset({'W','U','B','R','G','C','GENERIC'})


class TurnLimitedAlternativeScope(str, Enum):
    TIME_COUNTER_LIMIT = "time_counter_limit"
    EXILED_CREATURE = "exiled_creature"
    EXILED_SPELL = "exiled_spell"
    COLORLESS_FROM_HAND = "colorless_from_hand"
    NONOWNED_MANA_VALUE_THREE = "nonowned_mana_value_three"

    def selection(self) -> tuple[str, ObjectQuerySpec]:
        if self is TurnLimitedAlternativeScope.EXILED_CREATURE:
            return "exile", ObjectQuerySpec(types_all=("creature",))
        if self is TurnLimitedAlternativeScope.EXILED_SPELL:
            return "exile", ObjectQuerySpec()
        if self is TurnLimitedAlternativeScope.COLORLESS_FROM_HAND:
            return "hand", ObjectQuerySpec(colorless=True)
        return "any", ObjectQuerySpec()

    def permits_value_and_owner(self, *, mana_value: int | float, actor: str, owner: str, time_counters: int) -> bool:
        if type(mana_value) not in (int, float) or not math.isfinite(mana_value) or mana_value < 0:
            raise ValueError("Alternative casting requires an exact nonnegative mana value")
        if not actor or not owner or type(actor) is not str or type(owner) is not str:
            raise ValueError("Alternative casting requires its actor and owner")
        if type(time_counters) is not int or time_counters < 0:
            raise ValueError("Alternative casting requires a valid current time-counter count")
        if self is TurnLimitedAlternativeScope.TIME_COUNTER_LIMIT:
            return mana_value <= time_counters
        if self is TurnLimitedAlternativeScope.NONOWNED_MANA_VALUE_THREE:
            return owner != actor and mana_value <= 3
        return True


@dataclass(frozen=True,slots=True)
class GlobalAlternativeCostSpec:
    mana: FrozenMap
    predicate: ObjectQuerySpec=ObjectQuerySpec()
    origin: str='any'
    without_mana_cost: bool=False
    turn_limited_scope: TurnLimitedAlternativeScope | None = None

    def __post_init__(self):
        if not isinstance(self.mana,FrozenMap):
            if not isinstance(self.mana,Mapping):raise ValueError('Global alternative mana must be an object')
            object.__setattr__(self,'mana',FrozenMap(self.mana))
        if set(self.mana)!=MANA_KEYS or any(type(v) is not int or v<0 for v in self.mana.values()):
            raise ValueError('Global alternative mana requires canonical nonnegative amounts')
        if type(self.without_mana_cost) is not bool:
            raise ValueError("Global alternative payment mode must be boolean")
        if self.without_mana_cost and any(self.mana.values()):
            raise ValueError("Without-mana alternative cannot charge a base mana cost")
        if self.turn_limited_scope is not None:
            if not isinstance(self.turn_limited_scope, TurnLimitedAlternativeScope):
                raise ValueError("Turn-limited alternative requires a typed scope")
            if not self.without_mana_cost or (self.origin, self.predicate) != self.turn_limited_scope.selection():
                raise ValueError("Turn-limited alternative requires its exact zero-mana price and selector")
        elif (
            self.origin not in {"any", "hand"} or not isinstance(self.predicate, ObjectQuerySpec)
            or self.predicate != ObjectQuerySpec(
                types_all=self.predicate.types_all, subtypes_all=self.predicate.subtypes_all,
            )
        ):
            raise ValueError("Fixed global alternative requires the original type and origin scope")

    def to_dict(self):
        value = {"schema_version": 1, "mana": thaw_value(self.mana), "predicate": self.predicate.to_dict(),
                 "origin": self.origin, "without_mana_cost": self.without_mana_cost}
        if self.turn_limited_scope is not None:
            value.update(schema_version=2, turn_limited_scope=self.turn_limited_scope.value)
        return value

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int:
            raise ValueError("Global alternative requires a versioned object")
        fields = {"schema_version", "mana", "predicate", "origin", "without_mana_cost"}
        version = value["schema_version"]
        scope = None
        if version == 2:
            fields.add("turn_limited_scope")
        if version not in (1, 2) or set(value) != fields:
            raise ValueError("Global alternative fields do not match their version")
        if version == 2:
            scope = TurnLimitedAlternativeScope(value["turn_limited_scope"])
        return cls(FrozenMap(value["mana"]), ObjectQuerySpec.from_dict(value["predicate"]),
                   value["origin"], value["without_mana_cost"], scope)
