from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


STATIC_PLAYER_RESTRICTION_CAPABILITY = 'actions.restriction.static_player'
ATTACHED_CAST_RESTRICTION_CAPABILITY = 'casting.prohibition.source_attachment'


class StaticPlayerRestrictionKind(StrEnum):
    ACTOR_OTHER_TURN = 'actor_other_turn'
    OPPONENT_NONHAND = 'opponent_nonhand'
    NONARTIFACT_LIMIT = 'nonartifact_limit'
    SOURCE_TURN_OPPONENTS = 'source_turn_opponents'


@dataclass(frozen=True, slots=True)
class StaticPlayerRestrictionSpec:
    kind: StaticPlayerRestrictionKind
    schema_version: int = 1
    requires_source_attached_creature: bool = False

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {1,2}:
            raise ValueError('Static player restriction schema is unsupported')
        if not isinstance(self.kind, StaticPlayerRestrictionKind):
            raise ValueError('Static player restriction kind must be typed')
        if (type(self.requires_source_attached_creature) is not bool
                or self.requires_source_attached_creature != (self.schema_version==2)
                or (self.schema_version==2 and self.kind is not StaticPlayerRestrictionKind.SOURCE_TURN_OPPONENTS)):
            raise ValueError('Attached-source restrictions require their explicit source-turn schema')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version':self.schema_version,'kind':self.kind.value,
                **({'requires_source_attached_creature':True} if self.schema_version==2 else {})}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StaticPlayerRestrictionSpec:
        if not isinstance(value,Mapping):raise ValueError('Static player restriction must be an object')
        fields={'schema_version','kind'}|({'requires_source_attached_creature'} if value.get('schema_version')==2 else set())
        if set(value)!=fields:
            raise ValueError('Static player restriction fields are closed')
        return cls(StaticPlayerRestrictionKind(value['kind']),value['schema_version'],value.get('requires_source_attached_creature',False))

    def applies(self, *, actor: str, active_player: str, source_controller: str, source_attached_creature: bool=False) -> bool:
        if not all(type(value) is str and value for value in (actor, active_player, source_controller)):
            raise ValueError('Static player restrictions require current player identities')
        if type(source_attached_creature) is not bool:raise ValueError('Source attachment information must be boolean')
        if self.requires_source_attached_creature and not source_attached_creature:return False
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
            spec=StaticPlayerRestrictionSpec.from_dict(value['player_restriction'])
            own=(STATIC_PLAYER_RESTRICTION_CAPABILITY,*((ATTACHED_CAST_RESTRICTION_CAPABILITY,) if spec.requires_source_attached_creature else ()))
        return tuple(sorted({*own, *(cap for child in value.values()
            for cap in static_player_restriction_capabilities(child))}))
    if isinstance(value, (tuple, list)):
        return tuple(sorted({cap for child in value for cap in static_player_restriction_capabilities(child)}))
    return ()
