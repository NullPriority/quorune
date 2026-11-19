from __future__ import annotations

"""One closed permission to cast a bounded spell from hand during resolution."""

from collections.abc import Mapping
from dataclasses import dataclass
from .mana_value_limit import ManaValueLimitSpec
from .replacement.immutable import FrozenMap, thaw_value

BOUNDED_HAND_CAST_FIELD = 'bounded_hand_cast'
BOUNDED_HAND_CAST_CAPABILITY = 'casting.resolution.fixed_bounded_hand'
BOUNDED_HAND_CAST_MECHANIC = 'fixed-bounded-hand-free-cast'


@dataclass(frozen=True, slots=True)
class BoundedHandCastSpec:
    mana_value_limit: ManaValueLimitSpec
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.mana_value_limit, ManaValueLimitSpec)):
            raise ValueError('Hand free cast requires its closed fixed mana-value limit')

    def to_dict(self):
        return {'schema_version': 1, 'mana_value_limit': self.mana_value_limit.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'mana_value_limit'}:
            raise ValueError('Hand free cast permission has unknown or missing fields')
        return cls(ManaValueLimitSpec.from_dict(value['mana_value_limit']), value['schema_version'])

    def effect(self):
        return {'op': 'choose_objects', 'player': '$controller', BOUNDED_HAND_CAST_FIELD: self.to_dict()}

    @classmethod
    def from_effect(cls, effect, *, actor='$controller'):
        if (not isinstance(effect, Mapping) or set(effect) != {'op', 'player', BOUNDED_HAND_CAST_FIELD}
                or effect['op'] != 'choose_objects' or effect['player'] != actor):
            raise ValueError('Hand free cast requires one complete controller instruction')
        return cls.from_dict(effect[BOUNDED_HAND_CAST_FIELD])

    def compiled(self):
        return ('fixed-bounded-hand-free-cast-v1', (self.effect(),), None, (BOUNDED_HAND_CAST_MECHANIC,))


def bounded_hand_cast_payload_dependencies(effect):
    if not isinstance(effect, Mapping) or BOUNDED_HAND_CAST_FIELD not in effect:
        return ()
    BoundedHandCastSpec.from_effect(effect)
    return (BOUNDED_HAND_CAST_CAPABILITY,)


def bounded_hand_cast_node_capabilities(*, effects, target_schema, mechanic_ids=()):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        return bounded_hand_cast_payload_dependencies(effects[0])
    except (ValueError, TypeError, KeyError):
        return ()


def is_closed_bounded_hand_cast_program(program):
    return bool(BOUNDED_HAND_CAST_MECHANIC in program.coverage
        and bounded_hand_cast_node_capabilities(effects=program.effects, target_schema=program.target_schema))


@dataclass(frozen=True, slots=True)
class ResolveBoundedHandCastIntent:
    actor: str
    stack_ref: str
    spec: BoundedHandCastSpec
    options_fingerprint: str
    response: FrozenMap | None
    replacement_selections: tuple = ()

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor or type(self.stack_ref) is not str or not self.stack_ref
                or not isinstance(self.spec, BoundedHandCastSpec)
                or type(self.options_fingerprint) is not str or len(self.options_fingerprint) != 64
                or any(c not in '0123456789abcdef' for c in self.options_fingerprint)
                or self.response is not None and not isinstance(self.response, FrozenMap)
                or not isinstance(self.replacement_selections, tuple)):
            raise ValueError('Hand free cast requires its original stack, permission, options and response')
        if self.response is not None and any(type(k) is not str or k.startswith('_') for k in self.response):
            raise ValueError('Internal hand free cast fields cannot be submitted')

    def identity(self):
        return {'actor': self.actor, 'stack_ref': self.stack_ref, 'spec': self.spec.to_dict(),
                'options_fingerprint': self.options_fingerprint, 'response': thaw_value(self.response)}


def validate_bounded_hand_cast_identity(value):
    if (not isinstance(value, Mapping)
            or set(value) != {'actor', 'stack_ref', 'spec', 'options_fingerprint', 'response'}):
        raise ValueError('The resolving hand free cast identity is malformed')
    response = value['response']
    if response is not None and not isinstance(response, Mapping):
        raise ValueError('The resolving hand free cast response is malformed')
    return ResolveBoundedHandCastIntent(value['actor'], value['stack_ref'], BoundedHandCastSpec.from_dict(value['spec']),
        value['options_fingerprint'], FrozenMap(response) if response is not None else None).identity()
