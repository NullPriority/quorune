"""Immutable Ripple count and cast-time public names; existing owners cast and move."""
from collections.abc import Mapping
from dataclasses import dataclass

RIPPLE_CAPABILITY = 'keyword.ripple.fixed_count'


def _public_names(value):
    if (not isinstance(value,tuple) or any(type(n) is not str or not n or n!=n.strip() for n in value)
            or tuple(sorted(set(value)))!=value):
        raise ValueError('Ripple names require canonical immutable known public facts')
    return value


@dataclass(frozen=True,slots=True)
class RippleSpec:
    count: int
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or type(self.count) is not int or self.count<0:
            raise ValueError('Ripple requires its version and a fixed nonnegative count')

    def to_dict(self):
        return {'schema_version':1,'count':self.count}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','count'}:
            raise ValueError('Ripple has a closed numeric descriptor')
        return cls(**value)


@dataclass(frozen=True,slots=True)
class RippleCastSnapshot:
    controller: str
    source_spell_ref: str
    rules_names: tuple[str,...]
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or any(type(v) is not str or not v or v!=v.strip() for v in (self.controller,self.source_spell_ref))):
            raise ValueError('Ripple cast snapshot requires its actual controller, spell identity and version')
        _public_names(self.rules_names)

    def matches_names(self,names):
        return bool(set(self.rules_names).intersection(_public_names(names)))

    def to_dict(self):
        return {'schema_version':1,'controller':self.controller,'source_spell_ref':self.source_spell_ref,'rules_names':list(self.rules_names)}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','controller','source_spell_ref','rules_names'}:
            raise ValueError('Ripple cast snapshot fields are closed')
        names=value['rules_names']
        if not isinstance(names,(list,tuple)):
            raise ValueError('Ripple public names cannot be unknown or a scalar')
        return cls(controller=value['controller'],source_spell_ref=value['source_spell_ref'],rules_names=tuple(names),schema_version=value['schema_version'])
