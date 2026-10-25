from __future__ import annotations

"""Closed resolution-time costs for the existing optional-payment owner."""

from dataclasses import dataclass
from typing import Any, Mapping

from .object_predicate import ObjectQuerySpec
from .creature_subtypes import canonical_creature_subtype
from .counter_names import normalized_counter_name
from .replacement.immutable import FrozenMap, thaw_value


FIXED_EFFECT_PAYMENT_MECHANIC = "fixed-optional-mana-payment"
FIXED_EFFECT_PAYMENT_CAPABILITY = "effect.choice.optional_fixed_mana_payment"
MANA_DISCARD_PAYMENT_CAPABILITY = 'effect.choice.optional_mana_discard_payment'
_MANA_KEYS = frozenset({"GENERIC", "W", "U", "B", "R", "G", "C"})


def _validate_fixed_tap_predicate(predicate: ObjectQuerySpec | None) -> None:
    if not isinstance(predicate,ObjectQuerySpec):
        raise ValueError('Tap payments require a closed current-object predicate')
    allowed=ObjectQuerySpec(zones=('battlefield',),controller=predicate.controller,
        tapped=False,known_to_actor=True,exclude_ref=predicate.exclude_ref,
        types_all=predicate.types_all,subtypes_all=predicate.subtypes_all,
        excluded_subtypes=predicate.excluded_subtypes)
    if (predicate!=allowed or type(allowed.controller) is not str or not allowed.controller
            or not set(allowed.types_all)<={'artifact','battle','creature','enchantment','land','planeswalker'}
            or not (allowed.types_all or allowed.subtypes_all)
            or any(canonical_creature_subtype(value)!=value and value not in {
                'food','gate','forest','island','mountain','plains','swamp'} for value in allowed.subtypes_all)
            or allowed.excluded_subtypes not in ((),('human',))
            or allowed.excluded_subtypes and allowed.types_all!=('creature',)):
        raise ValueError('Tap payment predicate is outside the closed controlled untapped family')


@dataclass(frozen=True, slots=True)
class FixedEffectPaymentSpec:
    kind: str
    amount: int = 1
    requirements: FrozenMap | None = None
    predicate: ObjectQuerySpec | None = None
    schema_version: int = 2
    counter_name: str | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        version=5 if self.kind=='mana_discard' else 4 if self.kind=='remove_source_counter' else 3 if self.kind in {'exile','tap'} else 2
        if type(self.schema_version) is not int or self.schema_version != version:
            raise ValueError("Fixed effect payment version does not match its cost kind")
        if self.kind not in {"mana", "life", "discard", "sacrifice", "energy", "exile", "tap", "remove_source_counter", "mana_discard"}:
            raise ValueError("Fixed effect payment kind is unsupported")
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Fixed effect payment amount must be positive")
        if self.kind=='mana_discard':
            if self.amount!=1 or self.counter_name is not None or self.source_ref is not None:
                raise ValueError('Compound mana/discard cost requires exactly one ordinary hand card')
            mana=FixedEffectPaymentSpec('mana',requirements=self.requirements)
            FixedEffectPaymentSpec('discard',predicate=self.predicate)
            object.__setattr__(self,'requirements',mana.requirements)
            return
        if self.kind=='remove_source_counter':
            if (type(self.counter_name) is not str or not self.counter_name
                    or normalized_counter_name(self.counter_name)!=self.counter_name
                    or self.source_ref is not None and (type(self.source_ref) is not str or not self.source_ref)):
                raise ValueError('Source counter payments require a canonical named counter and optional current source')
        elif self.counter_name is not None or self.source_ref is not None:
            raise ValueError('Only source counter payments carry counter and source fields')
        if self.kind == "mana":
            if not isinstance(self.requirements, Mapping) or set(self.requirements) != _MANA_KEYS or any(
                type(value) is not int or value < 0 for value in self.requirements.values()
            ) or not any(self.requirements.values()) or self.amount != 1 or self.predicate is not None:
                raise ValueError("Fixed mana payments require one positive ordinary vector")
            object.__setattr__(self, "requirements", FrozenMap(self.requirements))
        elif self.requirements is not None:
            raise ValueError("Nonmana payments cannot carry mana requirements")
        if self.kind == 'tap':
            _validate_fixed_tap_predicate(self.predicate)
        elif self.kind in {"discard", "sacrifice", "exile"}:
            zone = {'discard':'hand','sacrifice':'battlefield','exile':'graveyard'}[self.kind]
            if (self.kind != 'exile' and self.amount not in ({1,2}if self.kind=='discard'else {1})) or not isinstance(self.predicate, ObjectQuerySpec) or self.predicate.zones != (zone,):
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
            if self.kind == 'exile' and (type(allowed.owner) is not str or not allowed.owner
                    or allowed.controller is not None or allowed.token is not False or allowed.known_to_actor is not True):
                raise ValueError('Graveyard exile costs require known owned cards without controller restrictions')
        elif self.predicate is not None:
            raise ValueError("Resource payments cannot carry object predicates")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version":self.schema_version, "kind":self.kind, "amount":self.amount,
                "requirements":thaw_value(self.requirements) if self.requirements is not None else None,
                "predicate":self.predicate.to_dict() if self.predicate is not None else None,
                **({'counter_name':self.counter_name,'source_ref':self.source_ref} if self.kind=='remove_source_counter' else {})}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedEffectPaymentSpec":
        fields={"schema_version", "kind", "amount", "requirements", "predicate"}
        if isinstance(value,Mapping) and value.get('kind')=='remove_source_counter':fields|={'counter_name','source_ref'}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("Fixed effect payment fields are malformed")
        return cls(kind=value["kind"], amount=value["amount"], schema_version=value["schema_version"],
            counter_name=value.get('counter_name'),source_ref=value.get('source_ref'),
            requirements=FrozenMap(value["requirements"]) if isinstance(value["requirements"],Mapping) else value["requirements"],
            predicate=ObjectQuerySpec.from_dict(value["predicate"]) if isinstance(value["predicate"],Mapping) else value["predicate"])

    @property
    def capabilities(self) -> tuple[str, ...]:
        costs = {"mana":(), "life":("casting.additional_cost.fixed_life_payment",),
                 'energy':('cost.energy.fixed_resolution_payment',),
                 "discard":("choice.affected_player.fixed_discard", "zone.change.destination_replacement"),
                 "sacrifice":("choice.affected_player.fixed_sacrifice", "zone.change.destination_replacement"),
                 'exile':('cost.exile.owned_graveyard_resolution','zone.change.destination_replacement'),
                 'tap':('cost.tap.controlled_untapped_resolution',),
                 'remove_source_counter':('cost.counter.source_fixed_resolution',)}
        costs['mana_discard']=(MANA_DISCARD_PAYMENT_CAPABILITY,'choice.affected_player.fixed_discard','zone.change.destination_replacement')
        return (FIXED_EFFECT_PAYMENT_CAPABILITY, *costs[self.kind])


def compound_payment_payload_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    if (not isinstance(value,Mapping) or value.get('op')!='offer_optional_mana_payment'
            or not isinstance(value.get('payment'),Mapping) or value['payment'].get('kind')!='mana_discard'):
        return ()
    payment=FixedEffectPaymentSpec.from_dict(value['payment'])
    if payment.predicate.owner != value.get('player'):
        raise ValueError('Combined payment requires its own payer hand')
    return payment.capabilities
