"""Closed all-player control restoration scopes over physical ownership."""
from collections.abc import Mapping
from dataclasses import dataclass
from .object_predicate import ObjectQuerySpec

OWNER_CONTROL_CAPABILITY='continuous.control.fixed_owner_restoration'
OWNER_CONTROL_MECHANIC='fixed-owner-control-restoration'
OWNER_CONTROL_OPERATION='restore_owner_control'


@dataclass(frozen=True,slots=True)
class OwnerControlSpec:
    scope: str

    def __post_init__(self):
        if type(self.scope) is not str or self.scope not in {'creatures','nontoken_permanents'}:
            raise ValueError('Owner control requires a closed permanent scope')

    def predicate(self):
        return (ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)) if self.scope=='creatures'
            else ObjectQuerySpec(zones=('battlefield',),token=False))


def validate_owner_control_effect(effect,*,allow_reason=False):
    fields={'op','schema_version','scope','source'}
    if (not isinstance(effect,Mapping) or not fields<=set(effect)
            or set(effect)-fields-({'reason'} if allow_reason else set())
            or effect['op']!=OWNER_CONTROL_OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=1
            or type(effect['source']) is not str or not effect['source']
            or 'reason' in effect and (type(effect['reason']) is not str or not effect['reason'])):
        raise ValueError('Owner control requires its closed source-backed fields')
    return OwnerControlSpec(effect['scope'])
