from __future__ import annotations

"""Current controller player protection without stored player keyword state."""

from dataclasses import dataclass
from typing import Mapping

from ..ability_fragments import StaticComponentSpec
from .component_registry import exact_fields
from .context import SemanticNodeError

PLAYER_TARGET_PROTECTION_HANDLER_ID='ability.static.controller-player-target-protection.v1'
PLAYER_TARGET_PROTECTION_CAPABILITY_ID='target.player.static_hexproof_shroud'


@dataclass(frozen=True,slots=True)
class PlayerTargetProtectionHandler:
    handler_id: str=PLAYER_TARGET_PROTECTION_HANDLER_ID
    schema_version: int=1
    family: str='ability.static.player_target_protection'
    event: str='characteristics.evaluate'
    rule_references: tuple[str,...]=('702.11c','702.18a','611.3a','611.3b','608.2b')
    capability_dependencies: tuple[str,...]=(PLAYER_TARGET_PROTECTION_CAPABILITY_ID,)

    def validate(self,descriptor):
        if not isinstance(descriptor,Mapping):raise SemanticNodeError('Player target protection requires a descriptor')
        exact_fields(descriptor,{'handler_id','schema_version','event','keyword'},field='player target protection')
        if descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1 or descriptor['event']!=self.event:
            raise SemanticNodeError('Player target protection identity is malformed')
        if type(descriptor['keyword']) is not str or descriptor['keyword'] not in {'hexproof','shroud'}:raise SemanticNodeError('Player target protection keyword is unsupported')
        return descriptor['keyword']

    def lower(self,descriptor,context):
        self.validate(descriptor)
        return ()


def current_player_target_keywords(host,seat):
    """Discover only current effective, trusted battlefield source components."""
    player=host.state.players.get(seat)
    if player is None:return frozenset()
    keywords=set();handler=PlayerTargetProtectionHandler()
    for object_id in player.zones['battlefield']:
        source=host.state.cards[object_id]
        if source.controller!=seat or source.phased_out:continue
        for fragment in host._effective_ability_fragments(source):
            if not isinstance(fragment,StaticComponentSpec):continue
            program=host.semantics.get(fragment.semantic_key)
            if program is None or not host.semantic_program_is_current_trusted(program):continue
            for descriptor in program.handlers:
                if descriptor.get('handler_id')==handler.handler_id:keywords.add(handler.validate(descriptor))
    return frozenset(keywords)
