from __future__ import annotations

"""Typed controller-wide permissions discovered from active CardPrograms."""

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..card_program_faces import program_matches_face
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import ObjectQueryResult, object_matches_query
from ..rules.capabilities import load_default_capability_registry
from .component_registry import (
    RuntimeComponentRegistry,
    exact_fields,
)
from .context import SemanticNodeError
from .current_ability_components import program_has_current_ability_fragments


ACTION_PERMISSION_EVENT = "action.permission"
LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID = (
    "permission.action.land-play-own-graveyard.v1"
)
ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID = (
    "permission.action.activate-controlled-creature-as-haste.v1"
)
LIBRARY_TOP_VISIBILITY_HANDLER_ID = (
    "permission.action.library-top-visibility.v1"
)
LIBRARY_TOP_ACTION_HANDLER_ID = "permission.action.library-top-action.v1"
ADDITIONAL_LAND_PLAY_HANDLER_ID = "permission.action.additional-land-play.v1"


class ActionPermissionKind(StrEnum):
    LAND_PLAY_FROM_OWN_GRAVEYARD = "land_play_from_own_graveyard"
    ACTIVATE_CONTROLLED_CREATURE_AS_HASTE = (
        "activate_controlled_creature_as_haste"
    )
    LIBRARY_TOP_VISIBILITY = "library_top_visibility"
    LIBRARY_TOP_ACTION = "library_top_action"
    ADDITIONAL_LAND_PLAY = "additional_land_play"


class LibraryTopVisibility(StrEnum):
    CONTROLLER = "controller"
    PUBLIC = "public"


class ActionPermissionScope(StrEnum):
    CONTROLLER = "controller"
    ALL_PLAYERS = "all_players"


@dataclass(frozen=True, slots=True)
class ActionPermissionSourceContext:
    source_ref: str
    source_logical_object_id: str
    source_semantic_key: str
    source_controller: str
    active_seats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StaticActionPermission:
    kind: ActionPermissionKind
    player: str
    source_ref: str
    source_logical_object_id: str
    source_semantic_key: str
    visibility: LibraryTopVisibility | None = None
    top_land_queries: tuple[ObjectQuerySpec, ...] = ()
    top_spell_queries: tuple[ObjectQuerySpec, ...] = ()
    additional_land_plays: int = 0


@dataclass(frozen=True, slots=True)
class StaticActionPermissionHandler:
    handler_id: str
    permission: ActionPermissionKind
    rule_references: tuple[str, ...]
    capability_dependencies: tuple[str, ...]
    schema_version: int = 1
    family: str = "permission.action.static"
    event: str = ACTION_PERMISSION_EVENT

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> ActionPermissionKind:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "permission"},
            field="Static action-permission handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Static action-permission handler ID mismatch"
            )
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported static action-permission schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Static action-permission handler must use {self.event}"
            )
        if descriptor["permission"] != self.permission.value:
            raise SemanticNodeError(
                "Static action-permission kind does not match its handler"
            )
        return self.permission

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ActionPermissionSourceContext,
    ) -> tuple[StaticActionPermission, ...]:
        permission = self.validate(descriptor)
        return (
            StaticActionPermission(
                kind=permission,
                player=context.source_controller,
                source_ref=context.source_ref,
                source_logical_object_id=context.source_logical_object_id,
                source_semantic_key=context.source_semantic_key,
            ),
        )


@dataclass(frozen=True, slots=True)
class LibraryTopVisibilityHandler:
    handler_id: str = LIBRARY_TOP_VISIBILITY_HANDLER_ID
    schema_version: int = 1
    family: str = "permission.action.static"
    event: str = ACTION_PERMISSION_EVENT
    rule_references: tuple[str, ...] = (
        "401.2",
        "401.3",
        "611.3b",
        "613.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "library.visibility.top.static",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> tuple[LibraryTopVisibility, ActionPermissionScope]:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "visibility", "scope"},
            field="Library-top visibility handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Library-top visibility handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported library-top visibility schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Library-top visibility handler must use {self.event}"
            )
        try:
            visibility = LibraryTopVisibility(descriptor["visibility"])
            scope = ActionPermissionScope(descriptor["scope"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(
                "Library-top visibility and scope must be closed values"
            ) from exc
        if (
            visibility is LibraryTopVisibility.CONTROLLER
            and scope is not ActionPermissionScope.CONTROLLER
        ):
            raise SemanticNodeError(
                "Private library-top visibility must be controller-scoped"
            )
        return visibility, scope

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ActionPermissionSourceContext,
    ) -> tuple[StaticActionPermission, ...]:
        visibility, scope = self.validate(descriptor)
        players = (
            context.active_seats
            if scope is ActionPermissionScope.ALL_PLAYERS
            else (context.source_controller,)
        )
        return tuple(
            StaticActionPermission(
                kind=ActionPermissionKind.LIBRARY_TOP_VISIBILITY,
                player=player,
                source_ref=context.source_ref,
                source_logical_object_id=context.source_logical_object_id,
                source_semantic_key=context.source_semantic_key,
                visibility=visibility,
            )
            for player in players
        )


@dataclass(frozen=True, slots=True)
class LibraryTopActionHandler:
    handler_id: str = LIBRARY_TOP_ACTION_HANDLER_ID
    schema_version: int = 1
    family: str = "permission.action.static"
    event: str = ACTION_PERMISSION_EVENT
    rule_references: tuple[str, ...] = (
        "118.2",
        "305.2",
        "601.2",
        "601.3",
    )
    capability_dependencies: tuple[str, ...] = (
        "library.action.top.static",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> tuple[tuple[ObjectQuerySpec, ...], tuple[ObjectQuerySpec, ...]]:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "land_queries",
                "spell_queries",
            },
            field="Library-top action handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Library-top action handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported library-top action schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Library-top action handler must use {self.event}"
            )
        raw_land_queries = descriptor["land_queries"]
        raw_spell_queries = descriptor["spell_queries"]
        if not isinstance(raw_land_queries, list) or not isinstance(
            raw_spell_queries, list
        ):
            raise SemanticNodeError(
                "Library-top land and spell queries must be arrays"
            )
        try:
            land_queries = tuple(
                ObjectQuerySpec.from_dict(value) for value in raw_land_queries
            )
            spell_queries = tuple(
                ObjectQuerySpec.from_dict(value) for value in raw_spell_queries
            )
        except (ObjectQueryError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if not land_queries and not spell_queries:
            raise SemanticNodeError(
                "Library-top action permission must allow a land or spell"
            )
        if any("land" not in query.types_all for query in land_queries):
            raise SemanticNodeError(
                "Library-top land queries must require the land type"
            )
        if any("land" not in query.excluded_types for query in spell_queries):
            raise SemanticNodeError(
                "Library-top spell queries must exclude the land type"
            )
        if (
            len(land_queries) != len({repr(query) for query in land_queries})
            or len(spell_queries)
            != len({repr(query) for query in spell_queries})
        ):
            raise SemanticNodeError(
                "Library-top action queries must be unique"
            )
        return land_queries, spell_queries

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ActionPermissionSourceContext,
    ) -> tuple[StaticActionPermission, ...]:
        land_queries, spell_queries = self.validate(descriptor)
        return (
            StaticActionPermission(
                kind=ActionPermissionKind.LIBRARY_TOP_ACTION,
                player=context.source_controller,
                source_ref=context.source_ref,
                source_logical_object_id=context.source_logical_object_id,
                source_semantic_key=context.source_semantic_key,
                top_land_queries=land_queries,
                top_spell_queries=spell_queries,
            ),
        )


@dataclass(frozen=True, slots=True)
class AdditionalLandPlayHandler:
    handler_id: str = ADDITIONAL_LAND_PLAY_HANDLER_ID
    schema_version: int = 1
    family: str = "permission.action.static"
    event: str = ACTION_PERMISSION_EVENT
    rule_references: tuple[str, ...] = ("305.2", "305.2a", "305.2b")
    capability_dependencies: tuple[str, ...] = (
        "land.play.additional.static",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> int:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "amount"},
            field="Additional land-play handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Additional land-play handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported additional land-play schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Additional land-play handler must use {self.event}"
            )
        amount = descriptor["amount"]
        if type(amount) is not int or amount not in {1, 2}:
            raise SemanticNodeError(
                "Additional land-play amount must be one or two"
            )
        return amount

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ActionPermissionSourceContext,
    ) -> tuple[StaticActionPermission, ...]:
        return (
            StaticActionPermission(
                kind=ActionPermissionKind.ADDITIONAL_LAND_PLAY,
                player=context.source_controller,
                source_ref=context.source_ref,
                source_logical_object_id=context.source_logical_object_id,
                source_semantic_key=context.source_semantic_key,
                additional_land_plays=self.validate(descriptor),
            ),
        )


class ActionPermissionRegistry(
    RuntimeComponentRegistry[
        ActionPermissionSourceContext,
        StaticActionPermission,
    ]
):
    pass


@lru_cache(maxsize=1)
def default_action_permission_registry() -> ActionPermissionRegistry:
    registry = ActionPermissionRegistry(
        (
            StaticActionPermissionHandler(
                handler_id=LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID,
                permission=(
                    ActionPermissionKind.LAND_PLAY_FROM_OWN_GRAVEYARD
                ),
                rule_references=("116.2a", "305.2", "305.2a"),
                capability_dependencies=(
                    "land.play.from_own_graveyard",
                ),
            ),
            StaticActionPermissionHandler(
                handler_id=(
                    ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID
                ),
                permission=(
                    ActionPermissionKind.ACTIVATE_CONTROLLED_CREATURE_AS_HASTE
                ),
                rule_references=("302.6", "602.5b", "702.10c"),
                capability_dependencies=(
                    "activation.permission.controlled_creature_as_haste",
                ),
            ),
            LibraryTopVisibilityHandler(),
            LibraryTopActionHandler(),
            AdditionalLandPlayHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


class ActionPermissionHost(Protocol):
    active_seats: Sequence[str]
    state: Any
    semantics: Any

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> Sequence[Any]: ...

    def card_record(self, card: Any) -> Any: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _public_object_query_result(self, card: Any) -> ObjectQueryResult: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def controller_action_permissions(
    host: ActionPermissionHost,
    player: str,
) -> tuple[StaticActionPermission, ...]:
    """Collect active, face-pinned permissions controlled by one player."""

    if player not in host.active_seats:
        return ()
    registry = default_action_permission_registry()
    permissions: list[StaticActionPermission] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if (
            source.zone != "battlefield"
            or source.phased_out
        ):
            continue
        record = host.card_record(source)
        if record is None:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event=ACTION_PERMISSION_EVENT,
        )
        for program in programs:
            if (
                not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, source)
                or (
                    CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                    and not program_has_current_ability_fragments(
                        program,
                        host._effective_card_data(source),
                    )
                )
            ):
                continue
            context = ActionPermissionSourceContext(
                source_ref=source.ref,
                source_logical_object_id=source.logical_object_id,
                source_semantic_key=program.key,
                source_controller=source.controller,
                active_seats=tuple(host.active_seats),
            )
            for descriptor in program.handlers:
                if registry.describe(
                    str(descriptor.get("handler_id") or "")
                ) is not None:
                    permissions.extend(
                        permission
                        for permission in registry.lower(descriptor, context)
                        if permission.player == player
                    )
    return tuple(
        sorted(
            permissions,
            key=lambda permission: (
                permission.kind.value,
                permission.source_ref,
                permission.source_semantic_key,
            ),
        )
    )


def controller_has_action_permission(
    host: ActionPermissionHost,
    player: str,
    kind: ActionPermissionKind,
) -> bool:
    return any(
        permission.kind is kind and permission.player == player
        for permission in controller_action_permissions(host, player)
    )


def _is_owned_library_top(
    host: ActionPermissionHost,
    player: str,
    card: Any,
) -> bool:
    library = host.state.players[player].zones["library"]
    return bool(
        card.owner == player
        and card.zone == "library"
        and library
        and library[-1] == card.object_id
    )


def controller_has_library_top_land_permission(
    host: ActionPermissionHost,
    player: str,
    card: Any,
) -> bool:
    if not _is_owned_library_top(host, player, card):
        return False
    row = host._public_object_query_result(card)
    return any(
        permission.kind is ActionPermissionKind.LIBRARY_TOP_ACTION
        and any(
            object_matches_query(row, query)
            for query in permission.top_land_queries
        )
        for permission in controller_action_permissions(host, player)
    )


def controller_has_library_top_spell_permission(
    host: ActionPermissionHost,
    player: str,
    card: Any,
) -> bool:
    if not _is_owned_library_top(host, player, card):
        return False
    row = host._public_object_query_result(card)
    return any(
        permission.kind is ActionPermissionKind.LIBRARY_TOP_ACTION
        and any(
            object_matches_query(row, query)
            for query in permission.top_spell_queries
        )
        for permission in controller_action_permissions(host, player)
    )


def additional_land_play_permission_slots(
    host: ActionPermissionHost,
    player: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            ":".join(
                (
                    permission.source_logical_object_id,
                    permission.source_semantic_key,
                    str(index),
                )
            )
            for permission in controller_action_permissions(host, player)
            if permission.kind is ActionPermissionKind.ADDITIONAL_LAND_PLAY
            for index in range(permission.additional_land_plays)
        )
    )


USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT = "used_additional_land_play_slots"


def available_additional_land_play_slots(
    host: ActionPermissionHost,
    player: str,
) -> tuple[str, ...]:
    used = host.state.players[player].stats.get(
        USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT,
        (),
    )
    if not isinstance(used, (list, tuple)) or any(
        type(value) is not str or not value for value in used
    ):
        raise SemanticNodeError(
            "Used additional land-play slots must be an array of identities"
        )
    return tuple(
        slot
        for slot in additional_land_play_permission_slots(host, player)
        if slot not in set(used)
    )


def land_play_permission_options(
    host: ActionPermissionHost,
    player: str,
) -> tuple[str, ...]:
    base = (
        ("base",)
        if host.state.players[player].land_plays_remaining > 0
        else ()
    )
    return (*base, *available_additional_land_play_slots(host, player))


def library_top_visibility(
    host: ActionPermissionHost,
    library_owner: str,
    viewer: str,
) -> LibraryTopVisibility | None:
    permissions = controller_action_permissions(host, library_owner)
    if any(
        permission.kind is ActionPermissionKind.LIBRARY_TOP_VISIBILITY
        and permission.visibility is LibraryTopVisibility.PUBLIC
        for permission in permissions
    ):
        return LibraryTopVisibility.PUBLIC
    if viewer == library_owner and any(
        permission.kind is ActionPermissionKind.LIBRARY_TOP_VISIBILITY
        and permission.visibility is LibraryTopVisibility.CONTROLLER
        for permission in permissions
    ):
        return LibraryTopVisibility.CONTROLLER
    return None


__all__ = [
    "ACTION_PERMISSION_EVENT",
    "ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID",
    "ADDITIONAL_LAND_PLAY_HANDLER_ID",
    "LIBRARY_TOP_ACTION_HANDLER_ID",
    "LIBRARY_TOP_VISIBILITY_HANDLER_ID",
    "ActionPermissionScope",
    "LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID",
    "ActionPermissionKind",
    "LibraryTopVisibility",
    "ActionPermissionRegistry",
    "ActionPermissionSourceContext",
    "StaticActionPermission",
    "StaticActionPermissionHandler",
    "USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT",
    "available_additional_land_play_slots",
    "additional_land_play_permission_slots",
    "controller_action_permissions",
    "controller_has_action_permission",
    "controller_has_library_top_land_permission",
    "controller_has_library_top_spell_permission",
    "default_action_permission_registry",
    "library_top_visibility",
    "land_play_permission_options",
]
