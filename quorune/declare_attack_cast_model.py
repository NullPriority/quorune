"""Typed printed cast window and declared-player attack facts."""
from collections.abc import Mapping
from dataclasses import dataclass

DECLARE_ATTACK_CAST_CAPABILITY = 'casting.restriction.declare_attackers_window'
DECLARE_ATTACK_CAST_HANDLER = 'ability.static.declare-attackers-cast-restriction.v1'
DECLARE_ATTACK_CAST_VERSION_FIELD = 'declare_attack_cast_version'


def declare_attack_cast_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError('Unsupported declared-attack cast-window provenance')
    return value


@dataclass(frozen=True, slots=True)
class DeclaredPlayerAttackFact:
    players: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.players, tuple)
                or any(type(p) is not str or not p for p in self.players)
                or self.players != tuple(sorted(set(self.players)))):
            raise ValueError('Declared player attacks require one canonical known player set')

    def to_dict(self): return {'schema_version': 1, 'players': list(self.players)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'players'}
                or not isinstance(value['players'], (list, tuple))):
            raise ValueError('Declared player attack fields are incomplete or unknown')
        return cls(tuple(value['players']), value['schema_version'])


@dataclass(frozen=True, slots=True)
class DeclareAttackCastSpec:
    require_attacked: bool
    schema_version: int = 1

    def __post_init__(self):
        if type(self.require_attacked) is not bool or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Attack-step casting requires its exact versioned requirement')

    def to_dict(self): return {'schema_version': 1, 'require_attacked': self.require_attacked}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'require_attacked'}:
            raise ValueError('Attack-step cast restriction fields are closed')
        return cls(**value)

    def permits(self, state, actor):
        if (actor not in state.players or state.phase != 'combat' or state.step != 'declare_attackers'
                or not state.combat.attackers_declared):
            return False
        if not self.require_attacked:
            return True
        if declare_attack_cast_version(getattr(state, DECLARE_ATTACK_CAST_VERSION_FIELD, None)) is None:
            return False
        fact = getattr(state.combat, 'declared_player_attacks', None)
        if not isinstance(fact, DeclaredPlayerAttackFact):
            return False
        return actor in fact.players
