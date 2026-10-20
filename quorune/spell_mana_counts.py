from __future__ import annotations

"""Versioned actual mana-unit payment facts, separate from copied decisions."""

from dataclasses import dataclass
from typing import Any, Mapping


MANA_SPENT_COUNTS_CAPABILITY = 'casting.payment.mana_unit_counts'
MANA_TYPES = 'WUBRGC'


def spell_mana_counts_version(value: Any) -> int | None:
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError('Unsupported spell mana-count provenance version')
    return value


@dataclass(frozen=True, slots=True)
class SpellManaSpentCounts:
    object_id: str
    logical_object_id: str
    amounts: tuple[int, ...]

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.object_id,self.logical_object_id)):
            raise ValueError('Mana payment facts require the original spell incarnation')
        if not isinstance(self.amounts,(tuple,list)) or len(self.amounts)!=len(MANA_TYPES) or any(
            type(value) is not int or value<0 for value in self.amounts
        ):
            raise ValueError('Mana payment counts require six nonnegative exact integers')
        object.__setattr__(self,'amounts',tuple(self.amounts))

    @classmethod
    def from_payment(cls, card: Any, spent: Mapping[str,int]) -> SpellManaSpentCounts:
        if not isinstance(spent,Mapping) or set(spent)-set(MANA_TYPES)-{'GENERIC'} or any(
            type(value) is not int or value<0 for value in spent.values()
        ) or spent.get('GENERIC',0):
            raise ValueError('Actual mana payment must use concrete mana types')
        return cls(card.object_id,card.logical_object_id,tuple(spent.get(color,0) for color in MANA_TYPES))

    def count_for(self, card: Any, color: str) -> int:
        if color not in MANA_TYPES or len(color)!=1:
            raise ValueError('Mana-unit lookup requires one concrete mana type')
        if (card.object_id,card.logical_object_id)!=(self.object_id,self.logical_object_id):
            raise ValueError('Mana payment facts belong to another spell incarnation')
        return self.amounts[MANA_TYPES.index(color)]

    def to_dict(self) -> dict[str,Any]:
        return {'schema_version':1,'object_id':self.object_id,'logical_object_id':self.logical_object_id,
            'amounts':dict(zip(MANA_TYPES,self.amounts,strict=True))}

    @classmethod
    def from_dict(cls,value: Mapping[str,Any]) -> SpellManaSpentCounts:
        if not isinstance(value,Mapping) or set(value)!={'schema_version','object_id','logical_object_id','amounts'}:
            raise ValueError('Mana payment facts use a closed schema')
        if type(value['schema_version']) is not int or value['schema_version']!=1:
            raise ValueError('Unsupported mana payment fact version')
        amounts=value['amounts']
        if not isinstance(amounts,Mapping) or set(amounts)!=set(MANA_TYPES):
            raise ValueError('Mana payment facts require every concrete mana type')
        return cls(value['object_id'],value['logical_object_id'],tuple(amounts[color] for color in MANA_TYPES))


def normalize_stack_mana_counts(kind: str, object_id: str | None, value: Any) -> SpellManaSpentCounts | None:
    if value is None:return None
    fact=value if isinstance(value,SpellManaSpentCounts) else SpellManaSpentCounts.from_dict(value)
    if kind!='spell' or fact.object_id!=object_id:
        raise ValueError('Only the original cast spell may retain actual mana payment facts')
    return fact


def capture_spell_mana_counts(version: int | None, card: Any, spent: Mapping[str,int]) -> SpellManaSpentCounts | None:
    return SpellManaSpentCounts.from_payment(card,spent) if spell_mana_counts_version(version) is not None else None
