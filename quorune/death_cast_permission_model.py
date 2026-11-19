"""Typed current-turn death predicates for a source's own graveyard cast."""
from collections.abc import Mapping
from dataclasses import dataclass
from .creature_subtypes import canonical_creature_subtype

DEATH_CAST_PERMISSION_CAPABILITY='casting.permission.self_graveyard_qualified_death'
DEATH_CAST_PERMISSION_HANDLER='casting.permission.self-graveyard-qualified-death.v1'


@dataclass(frozen=True,slots=True)
class DeathCastPermissionSpec:
    excluded_name: str | None=None
    excluded_subtype: str | None=None
    entry_counters: int=0
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or (self.excluded_name is None)==(self.excluded_subtype is None)
                or self.excluded_name is not None and (type(self.excluded_name) is not str or not self.excluded_name or self.excluded_name!=self.excluded_name.strip())
                or self.excluded_subtype is not None and canonical_creature_subtype(self.excluded_subtype)!=self.excluded_subtype
                or type(self.entry_counters) is not int or self.entry_counters not in (0,1)):
            raise ValueError('Qualified graveyard casting requires one explicit canonical death exclusion')

    def to_dict(self):
        return {'schema_version':1,'excluded_name':self.excluded_name,
            'excluded_subtype':self.excluded_subtype,'entry_counters':self.entry_counters}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','excluded_name','excluded_subtype','entry_counters'}:
            raise ValueError('Qualified death cast permission has a closed schema')
        return cls(**value)

    def matches_departure(self,*,was_creature,rules_names=None,creature_subtypes=None):
        if type(was_creature) is not bool:raise ValueError('Death predicate requires the actual departure type')
        if not was_creature:return False
        values=rules_names if self.excluded_name is not None else creature_subtypes
        if values is None:return None
        if (not isinstance(values,tuple) or any(type(v) is not str or not v or v!=v.strip() for v in values)
                or tuple(sorted(set(values)))!=values):
            raise ValueError('Death exclusions require canonical immutable departure facts')
        if self.excluded_subtype is not None and any(canonical_creature_subtype(v)!=v for v in values):
            raise ValueError('Death subtype facts must use the canonical creature vocabulary')
        excluded=self.excluded_name if self.excluded_name is not None else self.excluded_subtype
        return excluded not in values



DEATH_CAST_CHOICE_FIELD='qualified_death_cast'


def death_cast_choice(spec):
    if not isinstance(spec,DeathCastPermissionSpec):raise ValueError('A chosen death permission must be typed')
    return {'schema_version':1,'source_zone':'graveyard','permission':spec.to_dict()}


def validate_death_cast_choice(value):
    if (not isinstance(value,Mapping) or set(value)!={'schema_version','source_zone','permission'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or value['source_zone']!='graveyard'):
        raise ValueError('Chosen death casting authority requires its explicit version and graveyard origin')
    return DeathCastPermissionSpec.from_dict(value['permission'])
