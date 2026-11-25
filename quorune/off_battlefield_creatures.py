"""Intrinsic type and base stats for a source outside the battlefield."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

from .creature_subtypes import canonical_creature_subtype


if TYPE_CHECKING:
    from .continuous_effect_model import ContinuousEffect, ContinuousObjectIdentity


OFF_BATTLEFIELD_SELF_CREATURE_CAPABILITY = "continuous.characteristics.off_battlefield_self_creature"
OFF_BATTLEFIELD_SELF_CREATURE_HANDLER = "continuous.characteristics.off-battlefield-self-creature.v1"


_NONBATTLEFIELD_GAME_ZONES = ("command", "exile", "graveyard", "hand", "library", "stack")


@dataclass(frozen=True, slots=True)
class OffBattlefieldSelfCreatureSpec:
    power: int
    toughness: int
    subtype: str

    def __post_init__(self) -> None:
        if any(type(value) is not int or not 0 <= value <= 99
               for value in (self.power, self.toughness)):
            raise ValueError("Off-battlefield base stats require nonnegative two-digit integers")
        if not self.subtype or canonical_creature_subtype(self.subtype) != self.subtype:
            raise ValueError("Off-battlefield creature types require a canonical creature subtype")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OffBattlefieldSelfCreatureSpec:
        if not isinstance(value, Mapping) or set(value) != {"power", "toughness", "subtype"}:
            raise ValueError("Off-battlefield characteristics require exact base stats and one subtype")
        return cls(value["power"], value["toughness"], value["subtype"])

    def to_dict(self) -> dict[str, int | str]:
        return {"power": self.power, "toughness": self.toughness, "subtype": self.subtype}

    def layer_effects(
        self, source: ContinuousObjectIdentity, *, timestamp: int, occurrence: int = 0
    ) -> tuple[ContinuousEffect, ContinuousEffect]:
        from .continuous_effect_model import (
            ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousOperation, Layer,
        )
        from .object_predicate import ObjectQuerySpec

        effects = []
        for layer, sublayer, operations in (
            (
                Layer.TYPE,
                "4",
                (
                    ContinuousOperation("add_types", ("Creature",), field="card_types"),
                    ContinuousOperation("add_types", (self.subtype.title(),), field="subtypes"),
                ),
            ),
            (
                Layer.POWER_TOUGHNESS,
                "7b",
                (ContinuousOperation("set_power_toughness", (self.power, self.toughness)),),
            ),
        ):
            effects.append(ContinuousEffect(
                effect_id=f"{source.object_id}:off-battlefield-creature:{occurrence}:{sublayer}",
                source_id=source.object_id,
                layer=layer,
                sublayer=sublayer,
                timestamp=timestamp,
                operations=operations,
                origin=ContinuousEffectOrigin.STATIC_ABILITY,
                applies=ObjectQuerySpec(zones=_NONBATTLEFIELD_GAME_ZONES),
                relation=ContinuousEffectRelation.SOURCE_OBJECT,
                related_object=source,
            ))
        return effects[0], effects[1]
