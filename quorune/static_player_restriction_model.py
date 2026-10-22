from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


STATIC_PLAYER_RESTRICTION_CAPABILITY = 'actions.restriction.static_player'


class StaticPlayerRestrictionKind(StrEnum):
    ACTOR_OTHER_TURN = 'actor_other_turn'
    OPPONENT_NONHAND = 'opponent_nonhand'
    NONARTIFACT_LIMIT = 'nonartifact_limit'
    SOURCE_TURN_OPPONENTS = 'source_turn_opponents'


@dataclass(frozen=True, slots=True)
class StaticPlayerRestrictionSpec:
    kind: StaticPlayerRestrictionKind
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Static player restriction schema is unsupported')
        if not isinstance(self.kind, StaticPlayerRestrictionKind):
            raise ValueError('Static player restriction kind must be typed')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'kind': self.kind.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StaticPlayerRestrictionSpec:
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind'}:
            raise ValueError('Static player restriction fields are closed')
        return cls(StaticPlayerRestrictionKind(value['kind']), value['schema_version'])

    def applies(self, *, actor: str, active_player: str, source_controller: str) -> bool:
        if not all(type(value) is str and value for value in (actor, active_player, source_controller)):
            raise ValueError('Static player restrictions require current player identities')
        if self.kind is StaticPlayerRestrictionKind.ACTOR_OTHER_TURN:
            return actor != active_player
        if self.kind is StaticPlayerRestrictionKind.SOURCE_TURN_OPPONENTS:
            return actor != source_controller and active_player == source_controller
        return True


def static_player_restriction_capabilities(value: Any) -> tuple[str, ...]:
    """Infer the new authority from complete typed descriptor data."""
    if isinstance(value, Mapping):
        own = ()
        if 'player_restriction' in value:
            StaticPlayerRestrictionSpec.from_dict(value['player_restriction'])
            own = (STATIC_PLAYER_RESTRICTION_CAPABILITY,)
        return tuple(sorted({*own, *(cap for child in value.values()
            for cap in static_player_restriction_capabilities(child))}))
    if isinstance(value, (tuple, list)):
        return tuple(sorted({cap for child in value for cap in static_player_restriction_capabilities(child)}))
    return ()
