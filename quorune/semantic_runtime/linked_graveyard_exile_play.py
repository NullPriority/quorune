"""Read the live controller's own-turn permission over the exact linked exile."""

from dataclasses import dataclass

from ..rules.linked_graveyard_play import (
    PERMISSION_EVENT, PERMISSION_HANDLER, PLAY_DEPENDENCIES, LinkedGraveyardPlaySpec,
)
from .action_permissions import ActionPermissionKind, StaticActionPermission, controller_action_permissions
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class LinkedGraveyardPlayPermissionHandler:
    handler_id: str = PERMISSION_HANDLER
    schema_version: int = 1
    family: str = "permission.action.static"
    event: str = PERMISSION_EVENT
    rule_references: tuple[str, ...] = ("118.9", "305.2", "601.2", "607.2b")
    capability_dependencies: tuple[str, ...] = PLAY_DEPENDENCIES

    def validate(self, descriptor):
        try:
            return LinkedGraveyardPlaySpec.from_descriptor(descriptor, replacement=False)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        spec = self.validate(descriptor)
        return (StaticActionPermission(
            kind=ActionPermissionKind.LINKED_GRAVEYARD_EXILE_PLAY,
            player=context.source_controller,
            source_ref=context.source_ref,
            source_logical_object_id=context.source_logical_object_id,
            source_semantic_key=context.source_semantic_key,
            linked_exile_ability_pair=spec.ability_pair,
        ),)


def controller_linked_graveyard_exile_permissions(host, seat, card):
    if card.zone != "exile" or seat != host.state.active_player or card.face_down:
        return ()
    from ..linked_ability_exile_state import card_has_current_exile_link
    return tuple(
        permission for permission in controller_action_permissions(host, seat)
        if permission.kind is ActionPermissionKind.LINKED_GRAVEYARD_EXILE_PLAY
        and card_has_current_exile_link(
            card, source_incarnation=permission.source_logical_object_id,
            ability_pair=permission.linked_exile_ability_pair,
        )
    )
