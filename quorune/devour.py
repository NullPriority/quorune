from __future__ import annotations

"""Immutable Devour entry proposals; mutation belongs to canonical zone owners."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class DevourError(ValueError):
    """A Devour specification or selected incarnation is malformed."""

DEVOUR_HANDLER='replacement.zone.devour-entry.v1'
DEVOUR_CAPABILITY='zone.entry.devour'


class DevourQuality(StrEnum):
    CREATURE='creature'
    ARTIFACT='artifact'
    FOOD='food'


@dataclass(frozen=True,slots=True)
class DevourSpec:
    quality: DevourQuality
    multiplier: int | str

    def __post_init__(self):
        if not isinstance(self.quality,DevourQuality):
            try:object.__setattr__(self,'quality',DevourQuality(self.quality))
            except (ValueError,TypeError) as exc:raise DevourError('Devour quality is outside the closed family') from exc
        if (not (type(self.multiplier) is int and self.multiplier>=0)
                and not (self.multiplier=='devoured_count' and self.quality is DevourQuality.CREATURE)):
            raise DevourError('Devour needs a nonnegative fixed multiplier or its represented creature-count X')

    def counter_amount(self,count):
        if type(count) is not int or count<0:raise DevourError('Devour count must be a nonnegative integer')
        return count*(count if self.multiplier=='devoured_count' else self.multiplier)

    def to_dict(self):return {'schema_version':1,'quality':self.quality.value,'multiplier':self.multiplier}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','quality','multiplier'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise DevourError('Devour specification fields are closed')
        return cls(value['quality'],value['multiplier'])


@dataclass(frozen=True,slots=True)
class DevourObject:
    object_id: str
    logical_object_id: str
    ref: str

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.object_id,self.logical_object_id,self.ref)):
            raise DevourError('Devour requires complete physical, logical and public reference identity')

    def to_dict(self):return {'object_id':self.object_id,'logical_object_id':self.logical_object_id,'ref':self.ref}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'object_id','logical_object_id','ref'}:raise DevourError('Devour object fields are closed')
        return cls(**value)


@dataclass(frozen=True,slots=True)
class DevourProposal:
    controller: str
    component_id: str
    spec: DevourSpec
    objects: tuple[DevourObject,...]

    def __post_init__(self):
        if (type(self.controller) is not str or not self.controller or type(self.component_id) is not str or not self.component_id
                or not isinstance(self.spec,DevourSpec) or type(self.objects) is not tuple
                or any(not isinstance(v,DevourObject) for v in self.objects)
                or any(len({getattr(v,key) for v in self.objects})!=len(self.objects) for key in ('object_id','logical_object_id','ref'))):
            raise DevourError('Devour proposal must retain distinct selected incarnations and its controller')

    @property
    def counter_amount(self):return self.spec.counter_amount(len(self.objects))

    def to_dict(self):return {'schema_version':1,'controller':self.controller,'component_id':self.component_id,
        'spec':self.spec.to_dict(),'objects':[o.to_dict() for o in self.objects]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','controller','component_id','spec','objects'}
                or type(value['schema_version']) is not int or value['schema_version']!=1 or not isinstance(value['objects'],(list,tuple))):
            raise DevourError('Devour proposal fields are closed')
        return cls(value['controller'],value['component_id'],DevourSpec.from_dict(value['spec']),tuple(DevourObject.from_dict(o) for o in value['objects']))
