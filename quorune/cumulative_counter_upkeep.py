from __future__ import annotations

"""Closed per-age counter placement costs; each placement remains separate."""
from dataclasses import dataclass
import re
from typing import Mapping

COUNTER_PAYMENT_CAPABILITY='cost.counter.placement_resolution'
CUMULATIVE_COUNTER_CAPABILITY='counter.producer.cumulative_upkeep_counter_costs'

@dataclass(frozen=True, slots=True)
class CounterPlacementCostSpec:
    counter_name: str
    recipient_scope: str
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version)is not int or self.schema_version!=1:
            raise ValueError('Counter placement price requires descriptor one')
        if type(self.counter_name)is not str or type(self.recipient_scope)is not str or self.counter_name not in {'+1/+1','-1/-1'} or self.recipient_scope not in {'source','opponent_creature'}:
            raise ValueError('Counter price has an unrepresented counter or recipient scope')

    def to_dict(self):
        return {'schema_version':self.schema_version,'counter_name':self.counter_name,'recipient_scope':self.recipient_scope}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','counter_name','recipient_scope'}:
            raise ValueError('Counter placement price fields are malformed')
        return cls(**value)

    def placements(self,refs):
        if not isinstance(refs,(list,tuple)) or any(type(ref)is not str or not ref or ref!=ref.strip() for ref in refs):
            raise ValueError('Counter cost recipients require canonical public references')
        # Preserve one placement per age; replacement/event granularity matters.
        return tuple((ref,self.counter_name,1) for ref in refs)

@dataclass(frozen=True, slots=True)
class CumulativeCounterUpkeepSpec:
    cost: CounterPlacementCostSpec
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version)is not int or self.schema_version!=1 or not isinstance(self.cost,CounterPlacementCostSpec):
            raise ValueError('Counter cumulative upkeep requires its typed version-one price')

    def effect(self):
        return {'op':'offer_optional_mana_payment','schema_version':8,'player':'$controller','source':'$source.zone_object','counter_payment':self.cost.to_dict()}

    @classmethod
    def from_effect(cls,value,*,actor='$controller',source='$source.zone_object'):
        fields={'op','schema_version','player','source','counter_payment'}
        if not isinstance(value,Mapping) or set(value)!=fields or value['op']!='offer_optional_mana_payment' or type(value['schema_version'])is not int or value['schema_version']!=8 or value['player']!=actor or value['source']!=source:
            raise ValueError('Counter upkeep requires its explicitly bound version-eight wrapper')
        return cls(CounterPlacementCostSpec.from_dict(value['counter_payment']))

    @property
    def capabilities(self):
        return (COUNTER_PAYMENT_CAPABILITY,CUMULATIVE_COUNTER_CAPABILITY,'counter.producer.fixed_player_effect','effect.choice.optional_fixed_mana_payment','trigger.source.fixed_maintenance','zone.change.destination_replacement')


def compile_counter_cumulative_upkeep(text):
    match=re.fullmatch(r'Cumulative upkeep[—–]\s*Put a (?P<counter>[+-]1/[+-]1) counter on (?P<scope>this (?:creature|artifact|enchantment|permanent)|a creature an opponent controls)\.?',text,re.I)
    if match is None or match['counter'] not in {'+1/+1','-1/-1'}:return None
    return CumulativeCounterUpkeepSpec(CounterPlacementCostSpec(match['counter'],'opponent_creature' if match['scope'].casefold()=='a creature an opponent controls' else 'source'))
