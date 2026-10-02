from __future__ import annotations

"""Closed resolution-time costs for the existing optional-payment owner."""

from dataclasses import dataclass
from typing import Any, Mapping

from .object_predicate import ObjectQuerySpec
from .creature_subtypes import canonical_creature_subtype
from .replacement.immutable import FrozenMap, thaw_value


FIXED_EFFECT_PAYMENT_MECHANIC = "fixed-optional-mana-payment"
FIXED_EFFECT_PAYMENT_CAPABILITY = "effect.choice.optional_fixed_mana_payment"
_MANA_KEYS = frozenset({"GENERIC", "W", "U", "B", "R", "G", "C"})


@dataclass(frozen=True, slots=True)
class FixedEffectPaymentSpec:
    kind: str
    amount: int = 1
    requirements: FrozenMap | None = None
    predicate: ObjectQuerySpec | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError("Fixed effect payments require version 2")
        if self.kind not in {"mana", "life", "discard", "sacrifice"}:
            raise ValueError("Fixed effect payment kind is unsupported")
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Fixed effect payment amount must be positive")
        if self.kind == "mana":
            if not isinstance(self.requirements, Mapping) or set(self.requirements) != _MANA_KEYS or any(
                type(value) is not int or value < 0 for value in self.requirements.values()
            ) or not any(self.requirements.values()) or self.amount != 1 or self.predicate is not None:
                raise ValueError("Fixed mana payments require one positive ordinary vector")
            object.__setattr__(self, "requirements", FrozenMap(self.requirements))
        elif self.requirements is not None:
            raise ValueError("Nonmana payments cannot carry mana requirements")
        if self.kind in {"discard", "sacrifice"}:
            zone = "hand" if self.kind == "discard" else "battlefield"
            if self.amount not in ({1,2}if self.kind=='discard'else {1}) or not isinstance(self.predicate, ObjectQuerySpec) or self.predicate.zones != (zone,):
                raise ValueError("Zone payments require one closed current-object predicate")
            allowed = ObjectQuerySpec(zones=(zone,), owner=self.predicate.owner,
                controller=self.predicate.controller, types_all=self.predicate.types_all,
                types_any=self.predicate.types_any, colors_all=self.predicate.colors_all,
                colors_any=self.predicate.colors_any, excluded_types=self.predicate.excluded_types,
                subtypes_all=self.predicate.subtypes_all, subtypes_any=self.predicate.subtypes_any,
                supertypes_all=self.predicate.supertypes_all,token=self.predicate.token,
                known_to_actor=self.predicate.known_to_actor)
            if self.predicate != allowed or not set((*allowed.types_all, *allowed.types_any)) <= {
                "artifact", "battle", "creature", "enchantment", "instant", "land", "planeswalker", "sorcery"
            } or any(color not in "WUBRG" for color in (*allowed.colors_all, *allowed.colors_any)):
                raise ValueError("Zone payment predicate is outside the closed family")
            if not set(allowed.excluded_types)<={'artifact','creature','land'} or not set(allowed.supertypes_all)<={'basic','legendary','snow'} or any(
                value not in {'aura','clue','desert','food','forest','island','mountain','plains','room','swamp','treasure'} and canonical_creature_subtype(value)!=value
                for value in (*allowed.subtypes_all,*allowed.subtypes_any)
            ):
                raise ValueError('Zone payment quality is not a canonical represented characteristic')
            if self.kind=='discard'and (allowed.controller is not None or allowed.subtypes_all or allowed.subtypes_any or allowed.supertypes_all or allowed.excluded_types or allowed.token is not None):
                raise ValueError('Discard costs use only the closed owned-card quality')
        elif self.predicate is not None:
            raise ValueError("Resource payments cannot carry object predicates")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version":2, "kind":self.kind, "amount":self.amount,
                "requirements":thaw_value(self.requirements) if self.requirements is not None else None,
                "predicate":self.predicate.to_dict() if self.predicate is not None else None}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedEffectPaymentSpec":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "kind", "amount", "requirements", "predicate"}:
            raise ValueError("Fixed effect payment fields are malformed")
        return cls(kind=value["kind"], amount=value["amount"], schema_version=value["schema_version"],
            requirements=FrozenMap(value["requirements"]) if isinstance(value["requirements"],Mapping) else value["requirements"],
            predicate=ObjectQuerySpec.from_dict(value["predicate"]) if isinstance(value["predicate"],Mapping) else value["predicate"])

    @property
    def capabilities(self) -> tuple[str, ...]:
        costs = {"mana":(), "life":("casting.additional_cost.fixed_life_payment",),
                 "discard":("choice.affected_player.fixed_discard", "zone.change.destination_replacement"),
                 "sacrifice":("choice.affected_player.fixed_sacrifice", "zone.change.destination_replacement")}
        return (FIXED_EFFECT_PAYMENT_CAPABILITY, *costs[self.kind])
