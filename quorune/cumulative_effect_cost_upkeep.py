from __future__ import annotations

"""Closed beneficial actions paid as an entire cumulative-upkeep cost."""
from dataclasses import dataclass
from collections.abc import Mapping
import re

CUMULATIVE_EFFECT_COST_CAPABILITY='counter.producer.cumulative_upkeep_effect_costs'

@dataclass(frozen=True,slots=True)
class UpkeepEffectCostSpec:
    kind: str
    unit_amount: int = 1
    color: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version)is not int or self.schema_version!=1 or type(self.unit_amount)is not int or self.unit_amount<1:
            raise ValueError('Effect upkeep cost requires descriptor one and a positive unit amount')
        if self.kind=='add_mana':
            if type(self.color)is not str or self.color not in 'WUBRGC' or len(self.color)!=1:
                raise ValueError('Mana-benefit upkeep cost needs one fixed mana color')
        elif self.kind=='opponent_life_gain':
            if self.color is not None:raise ValueError('Life-benefit upkeep cost cannot carry mana')
        else:raise ValueError('Effect upkeep cost action is unrepresented')

    def to_dict(self):
        return {'schema_version':self.schema_version,'kind':self.kind,'unit_amount':self.unit_amount,'color':self.color}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','kind','unit_amount','color'}:
            raise ValueError('Effect upkeep cost descriptor fields are malformed')
        return cls(**value)

    def total(self,age):
        if type(age)is not int or age<0:raise ValueError('Committed age count must be a nonnegative integer')
        return self.unit_amount*age

    def recipient_allowed(self,recipient,*,actor,active_seats,denied_life_players=()):
        if type(recipient)is not str or type(actor)is not str or actor not in active_seats or recipient not in active_seats:
            return False
        return recipient==actor if self.kind=='add_mana' else recipient!=actor and recipient not in denied_life_players

    def occurrence(self,recipient,*,actor,active_seats,denied_life_players=()):
        if not self.recipient_allowed(recipient,actor=actor,active_seats=active_seats,denied_life_players=denied_life_players):
            raise ValueError('Effect upkeep cost recipient is not eligible')
        return (self.kind,recipient,self.unit_amount,self.color)

    def effect(self):
        return {'op':'offer_optional_mana_payment','schema_version':10,'player':'$controller','source':'$source.zone_object','upkeep_effect_cost':self.to_dict()}

    @classmethod
    def from_effect(cls,value,*,actor='$controller',source='$source.zone_object'):
        if not isinstance(value,Mapping) or set(value)!={'op','schema_version','player','source','upkeep_effect_cost'} or value['op']!='offer_optional_mana_payment' or type(value['schema_version'])is not int or value['schema_version']!=10 or value['player']!=actor or value['source']!=source:
            raise ValueError('Effect upkeep cost requires its bound version-ten wrapper')
        return cls.from_dict(value['upkeep_effect_cost'])

    @property
    def capabilities(self):
        benefit=('mana.production.fixed_resolution',) if self.kind=='add_mana' else ('life.change.effect','life.gain.prohibition.static')
        return (CUMULATIVE_EFFECT_COST_CAPABILITY,'counter.producer.fixed_player_effect','effect.choice.optional_fixed_mana_payment','trigger.source.fixed_maintenance','zone.change.destination_replacement',*benefit)


def compile_effect_cost_upkeep(text):
    mana=re.fullmatch(r'Cumulative upkeep[—–]\s*Add \{(?P<color>[WUBRGC])\}\.?',text,re.I)
    if mana is not None:return UpkeepEffectCostSpec('add_mana',color=mana['color'].upper())
    life=re.fullmatch(r'Cumulative upkeep[—–]\s*An opponent gains (?P<amount>[1-9]\d*) life\.?',text,re.I)
    return UpkeepEffectCostSpec('opponent_life_gain',unit_amount=int(life['amount'])) if life is not None else None
