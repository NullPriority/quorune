from __future__ import annotations

"""Current controller player protection without stored player keyword state."""

from dataclasses import dataclass
from typing import Mapping

from ..ability_fragments import StaticComponentSpec
from ..all_player_turn_rule_model import AllPlayerTurnRule, has_all_player_turn_rule
from ..qualified_hexproof import TURN_PLAYER_COLOR_HEXPROOF, color_hexproof_keywords
from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from .component_registry import exact_fields
from .context import SemanticNodeError

PLAYER_TARGET_PROTECTION_HANDLER_ID='ability.static.controller-player-target-protection.v1'
PLAYER_TARGET_PROTECTION_CAPABILITY_ID='target.player.static_hexproof_shroud'
CONTROLLER_TURN_PLAYER_HEXPROOF_HANDLER_ID = "ability.static.controller-turn-player-hexproof.v1"
CONTROLLER_TURN_PLAYER_HEXPROOF_CONDITION = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_TURN)


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
        return self._validate_keyword(descriptor)

    def _validate_keyword(self, descriptor):
        if descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1 or descriptor['event']!=self.event:
            raise SemanticNodeError('Player target protection identity is malformed')
        if type(descriptor['keyword']) is not str or descriptor['keyword'] not in {'hexproof','shroud'}:raise SemanticNodeError('Player target protection keyword is unsupported')
        return descriptor['keyword']

    def lower(self,descriptor,context):
        self.validate(descriptor)
        return ()


@dataclass(frozen=True, slots=True)
class ControllerTurnPlayerHexproofHandler(PlayerTargetProtectionHandler):
    """Derive player Hexproof only during the current source controller's turn."""

    handler_id: str = CONTROLLER_TURN_PLAYER_HEXPROOF_HANDLER_ID
    family: str = "ability.static.controller_turn_player_hexproof"
    rule_references: tuple[str, ...] = ("109.5", "611.3a", "702.11c", "608.2b")
    capability_dependencies: tuple[str, ...] = (
        PLAYER_TARGET_PROTECTION_CAPABILITY_ID, "continuous.characteristics.fixed_public_state",
    )

    def validate(self, descriptor):
        if not isinstance(descriptor, Mapping):
            raise SemanticNodeError("Turn-scoped player Hexproof requires a descriptor")
        exact_fields(
            descriptor, {"handler_id", "schema_version", "event", "keyword", "source_condition"},
            field="turn-scoped player Hexproof",
        )
        keyword = self._validate_keyword(descriptor)
        condition = FixedPublicStateConditionSpec.from_dict(descriptor["source_condition"])
        if keyword != "hexproof" or condition != CONTROLLER_TURN_PLAYER_HEXPROOF_CONDITION:
            raise SemanticNodeError("Turn-scoped player Hexproof requires the current controller's turn")
        return keyword

    def applies_to_turn(self, source_controller: str, active_player: str | None) -> bool:
        return source_controller == active_player


def current_player_target_keywords(host,seat):
    """Combine resolved player grants with current trusted battlefield sources."""
    player=host.state.players.get(seat)
    if player is None:return frozenset()
    keywords = set(color_hexproof_keywords(player.stats.get(TURN_PLAYER_COLOR_HEXPROOF, ())))
    if has_all_player_turn_rule(getattr(host.state, "continuous_effects", None), AllPlayerTurnRule.HEXPROOF):
        keywords.add("hexproof")
    handler = PlayerTargetProtectionHandler()
    controller_turn = ControllerTurnPlayerHexproofHandler()
    for object_id in player.zones['battlefield']:
        source=host.state.cards[object_id]
        if source.controller!=seat or source.phased_out:continue
        for fragment in host._effective_ability_fragments(source):
            if not isinstance(fragment,StaticComponentSpec):continue
            program=host.semantics.get(fragment.semantic_key)
            if program is None or not host.semantic_program_is_current_trusted(program):continue
            for descriptor in program.handlers:
                handler_id = descriptor.get("handler_id")
                if handler_id == handler.handler_id:
                    keywords.add(handler.validate(descriptor))
                elif (handler_id == controller_turn.handler_id
                      and controller_turn.applies_to_turn(source.controller, host.state.active_player)):
                    keywords.add(controller_turn.validate(descriptor))
    return frozenset(keywords)
