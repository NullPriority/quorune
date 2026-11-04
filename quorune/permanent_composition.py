from __future__ import annotations

"""Immutable physical topology, separate from copiable characteristics or state."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import re

from .continuous_effect_model import ContinuousObjectIdentity


class MergePosition(str,Enum):
    TOP='top'
    BOTTOM='bottom'


def _text(value):
    return type(value) is str and bool(value) and value.strip()==value


def _validate_identity(value):
    if (not isinstance(value,ContinuousObjectIdentity) or not _text(value.object_id)
            or not _text(value.logical_object_id)
            or re.fullmatch(r'.+@(?:0|[1-9][0-9]*)',value.logical_object_id) is None
            or value.logical_object_id.rsplit('@',1)[0]!=value.object_id):
        raise ValueError('Composition identities require an exact physical object and incarnation')


def _encode_identity(value):
    return {'object_id':value.object_id,'logical_object_id':value.logical_object_id}


def _decode_identity(value):
    if not isinstance(value,Mapping) or set(value)!={'object_id','logical_object_id'}:
        raise ValueError('Composition identities have a closed wire shape')
    identity=ContinuousObjectIdentity(value['object_id'],value['logical_object_id'])
    _validate_identity(identity)
    return identity


@dataclass(frozen=True,slots=True)
class PermanentComponent:
    """Identity before incorporation; a part is not another live permanent."""

    identity: ContinuousObjectIdentity
    owner: str
    is_token: bool=False
    commander_designation_id: str | None=None

    def __post_init__(self):
        _validate_identity(self.identity)
        if (not _text(self.owner) or type(self.is_token) is not bool
                or self.commander_designation_id is not None and (
                    not _text(self.commander_designation_id) or self.is_token)):
            raise ValueError('A physical component requires its owner, token kind and physical commander designation')

    def to_dict(self):
        return {'identity':_encode_identity(self.identity),'owner':self.owner,'is_token':self.is_token,
            'commander_designation_id':self.commander_designation_id}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'identity','owner','is_token','commander_designation_id'}:
            raise ValueError('Physical components have a closed wire shape')
        return cls(_decode_identity(value['identity']),value['owner'],value['is_token'],value['commander_designation_id'])


@dataclass(frozen=True,slots=True)
class PermanentComposition:
    """One retained object and ordered physical parts, never a copy recipe.

    This value owns no zone membership, characteristics, casting permission or
    mutation authority. Its callers must obtain current identities and facts
    from their authoritative owners before using a planned composition.
    """

    object_identity: ContinuousObjectIdentity
    components: tuple[PermanentComponent,...]
    mutation_count: int=0
    schema_version: int=1

    def __post_init__(self):
        _validate_identity(self.object_identity)
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.components,(list,tuple)) or not self.components
                or any(not isinstance(component,PermanentComponent) for component in self.components)):
            raise ValueError('A composition requires its closed version and nonempty typed parts')
        components=tuple(self.components)
        physical_ids=tuple(component.identity.object_id for component in components)
        commanders=tuple(component.commander_designation_id for component in components
            if component.commander_designation_id is not None)
        retained_part=next((component for component in components
            if component.identity.object_id==self.object_identity.object_id),None)
        if (len(physical_ids)!=len(set(physical_ids))
                or retained_part is not None and retained_part.identity!=self.object_identity
                or len({component.owner for component in components})!=1
                or len(commanders)!=len(set(commanders))
                or type(self.mutation_count) is not int or not 0<=self.mutation_count<len(components)):
            raise ValueError('Composition parts require unique physical identities, one retained incarnation, common ownership and valid merge history')
        object.__setattr__(self,'components',components)

    @classmethod
    def single(cls,component):
        if not isinstance(component,PermanentComponent):raise ValueError('A singleton requires a typed physical component')
        return cls(component.identity,(component,))

    @property
    def top(self):
        return self.components[0]

    @property
    def owner(self):
        return self.top.owner

    @property
    def is_token(self):
        return self.top.is_token

    @property
    def commander_designation_ids(self):
        return tuple(component.commander_designation_id for component in self.components
            if component.commander_designation_id is not None)

    @property
    def is_commander(self):
        return bool(self.commander_designation_ids)

    @property
    def physical_object_ids(self):
        return tuple(component.identity.object_id for component in self.components)

    def _require_current(self,expected_object):
        _validate_identity(expected_object)
        if expected_object!=self.object_identity:
            raise ValueError('A composition cannot be reused for another object incarnation')

    def merge(self,incoming,*,position,expected_object):
        self._require_current(expected_object)
        if (not isinstance(incoming,PermanentComponent) or not isinstance(position,MergePosition)
                or incoming.owner!=self.owner or incoming.identity.object_id in self.physical_object_ids):
            raise ValueError('A merge requires a distinct same-owner physical part and an explicit position')
        parts=(incoming,*self.components) if position is MergePosition.TOP else (*self.components,incoming)
        return PermanentComposition(self.object_identity,parts,self.mutation_count+1,self.schema_version)

    def departure_components(self,*,expected_object):
        self._require_current(expected_object)
        return self.components

    def to_dict(self):
        return {'schema_version':self.schema_version,'object':_encode_identity(self.object_identity),
            'components':[component.to_dict() for component in self.components],'mutation_count':self.mutation_count}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','object','components','mutation_count'}
                or not isinstance(value['components'],(list,tuple))):
            raise ValueError('Compositions have a closed versioned wire shape')
        return cls(_decode_identity(value['object']),tuple(PermanentComponent.from_dict(component)
            for component in value['components']),value['mutation_count'],value['schema_version'])
