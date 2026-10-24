from __future__ import annotations

"""Live public-hand permissions over the existing action-permission authority."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .action_permissions import (
    ACTION_PERMISSION_EVENT, ActionPermissionKind, ActionPermissionSourceContext,
    StaticActionPermission, controller_action_permissions,
)
from .context import SemanticNodeError
from .component_registry import exact_fields

HAND_VISIBILITY_HANDLER = 'permission.action.hand-visibility.v1'
HAND_VISIBILITY_CAPABILITY = 'hand.visibility.static'
HAND_VISIBILITY_RULES = ('109.5', '113.6', '402.1', '402.3', '611.3b', '701.20a', '701.20b')


class HandVisibilityScope(StrEnum):
    ALL_PLAYERS = 'all_players'
    SOURCE_OPPONENTS = 'source_opponents'


@dataclass(frozen=True, slots=True)
class StaticHandVisibilityHandler:
    handler_id: str = HAND_VISIBILITY_HANDLER
    schema_version: int = 1
    family: str = 'permission.action.static'
    event: str = ACTION_PERMISSION_EVENT
    rule_references: tuple[str, ...] = HAND_VISIBILITY_RULES
    capability_dependencies: tuple[str, ...] = (HAND_VISIBILITY_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> HandVisibilityScope:
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'scope'}, field='Static hand visibility')
        if descriptor['handler_id'] != self.handler_id or descriptor['event'] != self.event:
            raise SemanticNodeError('Static hand visibility handler or event mismatch')
        if type(descriptor['schema_version']) is not int or descriptor['schema_version'] != self.schema_version:
            raise SemanticNodeError('Unsupported static hand visibility version')
        try:
            return HandVisibilityScope(descriptor['scope'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError('Static hand visibility requires a closed player scope') from exc

    def lower(self, descriptor: Mapping[str, Any], context: ActionPermissionSourceContext) -> tuple[StaticActionPermission, ...]:
        scope = self.validate(descriptor)
        if context.source_controller not in context.active_seats:
            return ()
        return tuple(StaticActionPermission(
            kind=ActionPermissionKind.PUBLIC_HAND, player=player,
            source_ref=context.source_ref, source_logical_object_id=context.source_logical_object_id,
            source_semantic_key=context.source_semantic_key,
        ) for player in context.active_seats if scope is HandVisibilityScope.ALL_PLAYERS or player != context.source_controller)


def hand_is_public(host: Any, owner: str) -> bool:
    return any(permission.kind is ActionPermissionKind.PUBLIC_HAND
               for permission in controller_action_permissions(host, owner))


def public_hand_owners(host: Any) -> frozenset[str]:
    return frozenset(owner for owner in host.active_seats if hand_is_public(host, owner))


def hand_card_is_known_to(host: Any, card: Any, actor: str, *, public_owners: frozenset[str] | None = None) -> bool:
    return card.zone == 'hand' and (
        card.owner == actor or actor in card.known_to or actor in card.revealed_to
        or (card.owner in public_owners if public_owners is not None else hand_is_public(host, card.owner))
    )
