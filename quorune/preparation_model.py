from __future__ import annotations

"""Closed alternative spell characteristics and a prepared permanent's link."""

from collections.abc import Mapping
from dataclasses import dataclass

PREPARATION_CAPABILITY='card.preparation.fixed_entry_spell'
PREPARATION_HANDLER='replacement.zone.prepared-entry.v1'
PREPARE_SPELL_FIELD='_prepare_spell'
PREPARED_FIELD='prepared_designation'
PREPARE_COPY_FIELD='prepared_spell_copy'
PREPARE_ENTRY_FIELD='entry_prepared'


@dataclass(frozen=True,slots=True)
class PrepareSpellSpec:
    oracle_id: str
    face_id: str
    mana_cost: str
    mana_value: int | float
    type_line: str
    colors: tuple[str,...]

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.oracle_id,self.face_id,self.type_line))
                or type(self.mana_cost) is not str or type(self.mana_value) not in (int,float) or self.mana_value<0
                or self.type_line.split(' — ')[0] not in {'Instant','Sorcery'} or type(self.colors) is not tuple
                or any(value not in tuple('WUBRG') for value in self.colors) or len(set(self.colors))!=len(self.colors)):
            raise ValueError('Prepare spell needs closed instant/sorcery alternative characteristics')

    def to_dict(self):return {'schema_version':1,'oracle_id':self.oracle_id,'face_id':self.face_id,'mana_cost':self.mana_cost,
        'mana_value':self.mana_value,'type_line':self.type_line,'colors':list(self.colors)}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','oracle_id','face_id','mana_cost','mana_value','type_line','colors'}
                or type(value['schema_version']) is not int or value['schema_version']!=1 or not isinstance(value['colors'],(list,tuple))):
            raise ValueError('Prepare spell alternative characteristic fields are closed')
        return cls(value['oracle_id'],value['face_id'],value['mana_cost'],value['mana_value'],value['type_line'],tuple(value['colors']))


@dataclass(frozen=True,slots=True)
class PreparedDesignation:
    source_id: str
    source_ref: str
    source_identity: str
    spell: PrepareSpellSpec
    copy_id: str | None=None

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.source_id,self.source_ref,self.source_identity))
                or not isinstance(self.spell,PrepareSpellSpec) or self.copy_id is not None and (type(self.copy_id) is not str or not self.copy_id)):
            raise ValueError('Prepared designation requires its source incarnation and alternative spell')

    def to_dict(self):return {'schema_version':1,'source_id':self.source_id,'source_ref':self.source_ref,
        'source_identity':self.source_identity,'spell':self.spell.to_dict(),'copy_id':self.copy_id}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','source_id','source_ref','source_identity','spell','copy_id'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):raise ValueError('Prepared designation fields are closed')
        return cls(value['source_id'],value['source_ref'],value['source_identity'],PrepareSpellSpec.from_dict(value['spell']),value['copy_id'])
