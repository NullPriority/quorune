from __future__ import annotations

"""Explicit repeatable object costs for one cumulative-upkeep occurrence."""
from dataclasses import dataclass, replace
import re
from typing import Mapping
from .fixed_effect_payment import FixedEffectPaymentSpec
from .object_predicate import ObjectQuerySpec
from .source_maintenance import SourceMaintenanceSpec

CAPABILITY = 'counter.producer.cumulative_upkeep_fixed_objects'
WRAPPER_VERSION = 7
_FIELDS = {'op','schema_version','player','source','cumulative_payment'}

@dataclass(frozen=True, slots=True)
class CumulativeObjectUpkeepSpec:
    payment: FixedEffectPaymentSpec
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Object cumulative upkeep requires descriptor version one')
        p=self.payment
        if not isinstance(p,FixedEffectPaymentSpec) or p.schema_version != 2 or p.amount != 1:
            raise ValueError('Object upkeep repeats one fixed ordinary object cost')
        if p.kind=='discard':
            expected=ObjectQuerySpec(zones=('hand',),owner=p.predicate.owner)
        elif p.kind=='sacrifice' and len(p.predicate.types_all)==1 and p.predicate.types_all[0] in {'artifact','battle','creature','enchantment','land','planeswalker'}:
            expected=ObjectQuerySpec(zones=('battlefield',),controller=p.predicate.controller,types_all=p.predicate.types_all)
        else:raise ValueError('Object upkeep cost is outside its closed family')
        if p.predicate!=expected:raise ValueError('Object upkeep changed its cost predicate')

    def to_dict(self):
        return {'schema_version':self.schema_version,'payment_per_age':self.payment.to_dict()}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','payment_per_age'}:
            raise ValueError('Object upkeep descriptor fields are malformed')
        return cls(FixedEffectPaymentSpec.from_dict(value['payment_per_age']),value['schema_version'])

    def effect(self):
        return {'op':'offer_optional_mana_payment','schema_version':WRAPPER_VERSION,
            'player':'$controller','source':'$source.zone_object','cumulative_payment':self.to_dict()}

    @classmethod
    def from_effect(cls,effect,*,actor='$controller',source='$source.zone_object'):
        if not isinstance(effect,Mapping) or set(effect)!=_FIELDS or effect.get('op')!='offer_optional_mana_payment' or type(effect.get('schema_version'))is not int or effect['schema_version']!=WRAPPER_VERSION:
            raise ValueError('Object upkeep requires the closed version-seven wrapper')
        if effect['player']!=actor or effect['source']!=source:
            raise ValueError('Object upkeep changed its payer or source binding')
        spec=cls.from_dict(effect['cumulative_payment']);p=spec.payment
        if p.kind=='discard' and p.predicate.owner!=actor or p.kind=='sacrifice' and p.predicate.controller!=actor:
            raise ValueError('Object upkeep cost changed its payer relation')
        return spec

    def repeated_payment(self,age):
        if type(age)is not int or age<0:raise ValueError('Committed age count must be nonnegative')
        return replace(self.payment,amount=age,schema_version=12) if age else None

    @property
    def capabilities(self):
        return tuple(sorted({CAPABILITY,'cost.object.repeated_resolution','counter.producer.fixed_player_effect',*SourceMaintenanceSpec(self.payment).capabilities}))


def compile_cumulative_object_upkeep(text):
    m=re.fullmatch(r'Cumulative upkeep[—–]\s*(?:Sacrifice (?:a|an) (?P<type>artifact|battle|creature|enchantment|land|planeswalker)|(?P<discard>Discard a card))\.?',text,re.I)
    if m is None:return None
    payment=FixedEffectPaymentSpec('discard',predicate=ObjectQuerySpec(zones=('hand',),owner='$controller')) if m['discard'] else FixedEffectPaymentSpec('sacrifice',predicate=ObjectQuerySpec(zones=('battlefield',),controller='$controller',types_all=(m['type'].casefold(),)))
    return CumulativeObjectUpkeepSpec(payment)
