from __future__ import annotations

"""Typed CR702.193 pricing and usage marker with ordinary mana reductions."""

from dataclasses import dataclass
import re
from typing import Any, Mapping
from .replacement.immutable import FrozenMap

POWER_UP_CAPABILITY = 'activation.power_up.entered_turn_cost'
POWER_UP_UNCOMPILED = 'power-up activation price and usage are unrepresented'
MANA_KEYS = ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')


@dataclass(frozen=True, slots=True)
class PowerUpSpec:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported Power-up descriptor version')

    def to_dict(self):
        return {'schema_version': self.schema_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        if not isinstance(value, Mapping) or set(value) != {'schema_version'}:
            raise ValueError('Power-up descriptors have a closed schema')
        return cls(**dict(value))


def power_up_reduction_options(mana_cost: str) -> tuple[FrozenMap, ...]:
    if not isinstance(mana_cost, str):raise ValueError('Power-up requires a mana cost')
    symbols=re.findall(r'\{([^{}]+)\}',mana_cost)
    if ''.join('{'+symbol+'}' for symbol in symbols)!=mana_cost:
        raise ValueError('Power-up mana cost contains unsupported text')
    choices=[]
    for symbol in symbols:
        if symbol.isdigit():options=(('GENERIC',int(symbol)),)
        elif symbol in 'WUBRGC' and len(symbol)==1:options=((symbol,1),)
        elif symbol in {'X','Y','Z'}:options=(('GENERIC',0),)
        else:
            parts=symbol.split('/')
            if len(parts)==2 and parts[-1]=='P' and len(parts[0])==1 and parts[0] in 'WUBRG':options=((parts[0],1),)
            elif len(parts)==3 and parts[-1]=='P' and all(part in 'WUBRG' and len(part)==1 for part in parts[:2]):options=tuple((part,1) for part in parts[:2])
            elif len(parts)==2 and len(set(parts))==2 and all(part in 'WUBRGC' and len(part)==1 or part=='2' for part in parts):options=tuple(('GENERIC',2) if part=='2' else (part,1) for part in parts)
            else:raise ValueError('Power-up mana-cost symbol is unsupported')
        choices.append(options)
    empty=FrozenMap({key:0 for key in MANA_KEYS});variants={tuple(empty[key] for key in MANA_KEYS):empty}
    for alternatives in choices:
        expanded={}
        for previous in variants.values():
            for key,amount in alternatives:
                value=dict(previous);value[key]+=amount
                expanded[tuple(value[field] for field in MANA_KEYS)]=FrozenMap(value)
        if len(expanded)>128:raise ValueError('Power-up reduction alternatives exceed the closed bound')
        variants=expanded
    return tuple(variants[key] for key in sorted(variants))


def reduce_power_up_requirements(requirements, reduction):
    if not isinstance(requirements,Mapping) or not isinstance(reduction,Mapping) or (set(requirements)|set(reduction))-set(MANA_KEYS):
        raise ValueError('Power-up reductions require ordinary mana components')
    if any(type(value) is not int or value<0 for value in (*requirements.values(),*reduction.values())):
        raise ValueError('Power-up mana amounts must be nonnegative integers')
    result={key:requirements.get(key,0) for key in MANA_KEYS}
    generic=reduction.get('GENERIC',0)
    for key in MANA_KEYS[1:]:
        amount=reduction.get(key,0);matched=min(result[key],amount)
        result[key]-=matched;generic+=amount-matched
    result['GENERIC']=max(0,result['GENERIC']-generic)
    return FrozenMap(result)
