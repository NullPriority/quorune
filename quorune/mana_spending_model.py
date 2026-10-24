from __future__ import annotations

"""Finite as-though matching that always returns actual mana units spent."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

COLORS = ('W', 'U', 'B', 'R', 'G', 'C')


class ManaSpendingMode(StrEnum):
    WHITE_AS_RED = 'white_as_red'
    ANY_COLOR = 'any_color'
    ANY_TYPE = 'any_type'


def _counts(value: Mapping[str, int], *, requirements: bool = False) -> dict[str, int]:
    keys = (*COLORS, 'GENERIC') if requirements else COLORS
    if set(value)-set(keys) or any(type(n) is not int or n < 0 for n in value.values()):
        raise ValueError('Mana matching requires canonical nonnegative integer counts')
    return {key:value.get(key, 0) for key in keys}


@dataclass(frozen=True, slots=True)
class ManaSpendingPolicy:
    modes: frozenset[ManaSpendingMode]

    def __post_init__(self) -> None:
        if not isinstance(self.modes, frozenset) or any(not isinstance(mode, ManaSpendingMode) for mode in self.modes):
            raise ValueError('Mana-spending policy requires closed immutable modes')

    def permits(self, actual: str, required: str) -> bool:
        if actual not in COLORS or required not in COLORS:return False
        return actual == required or ManaSpendingMode.ANY_TYPE in self.modes or (
            required in 'WUBRG' and ManaSpendingMode.ANY_COLOR in self.modes
        ) or (actual == 'W' and required == 'R' and ManaSpendingMode.WHITE_AS_RED in self.modes)

    def allocate(self, pool: Mapping[str, int], requirements: Mapping[str, int], *, reserve: Mapping[str, int] | None = None) -> dict[str, int] | None:
        available = _counts(pool); need = _counts(requirements, requirements=True); held = _counts(reserve or {})
        for color in COLORS:
            available[color] -= held[color]
            if available[color] < 0:return None
        if sum(available.values()) < sum(need.values()):return None
        # Source -> six actual types -> six required types -> sink. Residual
        # edges allow reassignment, so one flexible unit cannot pay twice.
        size=14; sink=13; capacity=[[0]*size for _ in range(size)]
        for i,actual in enumerate(COLORS):
            capacity[0][1+i]=available[actual]
            for j,required in enumerate(COLORS):
                if self.permits(actual,required):capacity[1+i][7+j]=sum(need.values())
        for j,required in enumerate(COLORS):capacity[7+j][sink]=need[required]
        demand=sum(need[color] for color in COLORS); flow=0
        source_order=sorted(range(1,7),key=lambda i:(-min(available[COLORS[i-1]],need[COLORS[i-1]]),i))
        while flow<demand:
            parent=[-1]*size;parent[0]=0;queue=[0]
            for node in queue:
                for other in (source_order if node==0 else range(1,size)):
                    if parent[other]<0 and capacity[node][other]>0:
                        parent[other]=node;queue.append(other)
                if parent[sink]>=0:break
            if parent[sink]<0:return None
            amount=demand-flow;node=sink
            while node:amount=min(amount,capacity[parent[node]][node]);node=parent[node]
            node=sink
            while node:
                previous=parent[node];capacity[previous][node]-=amount;capacity[node][previous]+=amount;node=previous
            flow+=amount
        spent={color:available[color]-capacity[0][1+i] for i,color in enumerate(COLORS)}
        generic=need['GENERIC']
        for color in ('C','W','U','B','R','G'):
            amount=min(generic,available[color]-spent[color]);spent[color]+=amount;generic-=amount
        return None if generic else spent

    def pay(self, pool: Mapping[str, int], requirements: Mapping[str, int], payment: Mapping[str, int] | None) -> tuple[dict[str, int], dict[str, int]]:
        current=_counts(pool);need=_counts(requirements,requirements=True)
        if payment is None:
            spent=self.allocate(current,need)
        else:
            declared=_counts(payment)
            if any(declared[color]>current[color] for color in COLORS):
                raise ValueError('Declared mana is not available')
            if sum(declared.values())!=sum(need.values()):
                raise ValueError('Declared payment must spend exactly the required amount')
            spent=self.allocate(declared,need)
        if spent is None:raise ValueError('Mana does not satisfy the cost under current spending permissions')
        return ({color:current[color]-spent[color] for color in COLORS},spent)
