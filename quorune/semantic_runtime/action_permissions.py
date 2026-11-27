from __future__ import annotations
from ..entry_copy import copiable_oracle_id

"""Typed controller-wide permissions discovered from active CardPrograms."""

from collections import Counter
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import lru_cache
from math import ceil
from typing import Any, Mapping, Protocol, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..card_program_faces import program_matches_face
from ..continuous_conditions import (
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..library_search_model import LibrarySearchRuleSpec
from ..object_query import ObjectQueryResult, object_matches_query
from ..public_characteristic_aggregate_model import (
    CONTROLLED_CREATURES_POWER_QUERY, PublicCharacteristicAggregateSpec,
)
from ..public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY, PublicHistoryQuerySpec
from ..rules.capabilities import load_default_capability_registry
from .component_registry import (
    RuntimeComponentRegistry,
    exact_fields,
)
from .context import SemanticNodeError
from .current_ability_components import program_has_current_ability_fragments
from ..opening_reveal_model import OpeningRevealSpec,OPENING_REVEAL_CAPABILITY,OPENING_REVEAL_HANDLER


ACTION_PERMISSION_EVENT = "action.permission"
LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID = (
    "permission.action.land-play-own-graveyard.v1"
)
ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID = (
    "permission.action.activate-controlled-creature-as-haste.v1"
)
SOURCE_LOYALTY_INSTANT_CAPABILITY = "activation.permission.self_loyalty_instant"
LIBRARY_TOP_VISIBILITY_HANDLER_ID = (
    "permission.action.library-top-visibility.v1"
)
LIBRARY_TOP_ACTION_HANDLER_ID = "permission.action.library-top-action.v1"
LIBRARY_TOP_LIFE_ACTION_HANDLER_ID = "permission.action.library-top-mana-value-life.v1"
LIBRARY_TOP_LIFE_ACTION_CAPABILITY = "library.action.top.mana_value_life"
QUALIFIED_LIBRARY_TOP_HANDLER_ID = 'permission.action.library-top-action.v2'
QUALIFIED_LIBRARY_TOP_CAPABILITY = 'library.action.top.qualified'
LIBRARY_TOP_MANA_VALUE_CAPABILITY = 'library.action.top.mana_value'
LIBRARY_TOP_HISTORY_CAPABILITY = 'library.action.top.turn_history'
ADDITIONAL_LAND_PLAY_HANDLER_ID = "permission.action.additional-land-play.v1"
SHARED_ADDITIONAL_LAND_PLAY_HANDLER_ID = "permission.action.additional-land-play.all-players.v1"
SHARED_ADDITIONAL_LAND_PLAY_CAPABILITY = "land.play.additional.all_players"
OPENING_HAND_BATTLEFIELD_HANDLER_ID = "permission.action.opening-hand-battlefield.v1"
OPENING_HAND_BATTLEFIELD_CAPABILITY = "game.opening_hand.battlefield"
OPENING_HAND_LUCK_HANDLER_ID = "permission.action.opening-hand-luck.v1"
OPENING_HAND_LUCK_CAPABILITY = "game.opening_hand.nonstarter_luck_entry"


class ActionPermissionKind(StrEnum):
    MANA_WHITE_AS_RED = 'mana_white_as_red'
    MANA_ANY_COLOR = 'mana_any_color'
    MANA_ANY_TYPE_CREATURE = 'mana_any_type_creature'
    DOUBLE_TAP_MANA = "double_tap_mana"
    TRIPLE_TAP_MANA = "triple_tap_mana"
    TRIPLE_BASIC_LAND_TAP_MANA = "triple_basic_land_tap_mana"
    PUBLIC_HAND = 'public_hand'
    OPENING_HAND_BATTLEFIELD = "opening_hand_battlefield"
    OPENING_HAND_LUCK = "opening_hand_luck"
    OPENING_HAND_REVEAL = "opening_hand_reveal"
    LAND_PLAY_FROM_OWN_GRAVEYARD = "land_play_from_own_graveyard"
    ACTIVATE_CONTROLLED_CREATURE_AS_HASTE = (
        "activate_controlled_creature_as_haste"
    )
    ACTIVATE_SOURCE_LOYALTY_AT_INSTANT = "activate_source_loyalty_at_instant"
    ACTIVATE_ENTERED_SOURCE_LOYALTY_AT_INSTANT = "activate_entered_source_loyalty_at_instant"
    LIBRARY_TOP_VISIBILITY = "library_top_visibility"
    LIBRARY_TOP_ACTION = "library_top_action"
    ADDITIONAL_LAND_PLAY = "additional_land_play"
    LIBRARY_SEARCH_RULE = 'library_search_rule'


SOURCE_LOYALTY_INSTANT_HANDLERS = {
    ActionPermissionKind.ACTIVATE_SOURCE_LOYALTY_AT_INSTANT: "permission.action.self-loyalty-instant.v1",
    ActionPermissionKind.ACTIVATE_ENTERED_SOURCE_LOYALTY_AT_INSTANT: "permission.action.entered-self-loyalty-instant.v1",
}


class LibraryTopVisibility(StrEnum):
    CONTROLLER = "controller"
    PUBLIC = "public"


class ActionPermissionScope(StrEnum):
    CONTROLLER = "controller"
    ALL_PLAYERS = "all_players"


class LibraryTopSpellPrice(StrEnum):
    MANA_VALUE_IN_LIFE = "mana_value_in_life"


class LibraryTopQualifier(StrEnum):
    ATTACHED_TO_CREATURE = 'attached_to_creature'
    DISTINCT_CREATURE_POWERS = 'distinct_creature_powers'
    OPPONENT_MORE_LANDS = 'opponent_more_lands'
    CHOSEN_CREATURE_TYPE = 'chosen_creature_type'
    MINIMUM_SPELL_MANA_VALUE = 'minimum_spell_mana_value'
    ARTIFACT_OR_COLORLESS_SPELL = 'artifact_or_colorless_spell'
    SOURCE_ATTACKED_THIS_TURN = 'source_attacked_this_turn'
    CONTROLLER_CAST_THIS_TURN = 'controller_cast_this_turn'


_DISTINCT_POWER = PublicCharacteristicAggregateSpec(
    'power', 'distinct_count', 'source_controller', CONTROLLED_CREATURES_POWER_QUERY, schema_version=2,
)
_BATTLEFIELD_LAND = ObjectQuerySpec(zones=('battlefield',), types_all=('land',))
ARTIFACT_OR_COLORLESS_LIBRARY_SPELL_QUERIES = (
    ObjectQuerySpec(types_all=('artifact',), excluded_types=('land',)),
    ObjectQuerySpec(colorless=True, excluded_types=('land',)),
)
_LIBRARY_TOP_HISTORY_CONDITIONS = {
    LibraryTopQualifier.SOURCE_ATTACKED_THIS_TURN: FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST, amount=1, schema_version=4,
        fact=FixedPublicStateFact.SOURCE_ATTACKED_THIS_TURN,
    ),
    LibraryTopQualifier.CONTROLLER_CAST_THIS_TURN: FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.HISTORY_COUNT_AT_LEAST, amount=1, schema_version=8,
        history_query=PublicHistoryQuerySpec('spell_cast', 'controller'),
    ),
}


def library_top_qualifier_capabilities(qualifier: LibraryTopQualifier) -> tuple[str, ...]:
    additional = {
        LibraryTopQualifier.DISTINCT_CREATURE_POWERS: _DISTINCT_POWER.capability_dependencies,
        LibraryTopQualifier.MINIMUM_SPELL_MANA_VALUE: (LIBRARY_TOP_MANA_VALUE_CAPABILITY,),
        LibraryTopQualifier.SOURCE_ATTACKED_THIS_TURN: (LIBRARY_TOP_HISTORY_CAPABILITY,),
        LibraryTopQualifier.CONTROLLER_CAST_THIS_TURN: (
            LIBRARY_TOP_HISTORY_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY,
        ),
    }
    return (QUALIFIED_LIBRARY_TOP_CAPABILITY, *additional.get(qualifier, ()))


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
    opening_reveal_spec: OpeningRevealSpec | None = None
    library_search_rule: LibrarySearchRuleSpec | None = None
    library_top_qualifier: LibraryTopQualifier | None = None
    minimum_spell_mana_value: int | None = None
    top_spell_price: LibraryTopSpellPrice | None = None

    def __post_init__(self):
        if self.kind is ActionPermissionKind.LIBRARY_SEARCH_RULE and not isinstance(self.library_search_rule,LibrarySearchRuleSpec):
            raise SemanticNodeError('Library search permission requires its typed rule')


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
class OpeningHandBattlefieldPermissionHandler(StaticActionPermissionHandler):
    handler_id: str = OPENING_HAND_BATTLEFIELD_HANDLER_ID
    permission: ActionPermissionKind = ActionPermissionKind.OPENING_HAND_BATTLEFIELD
    rule_references: tuple[str,...] = ('103.6','103.6a')
    capability_dependencies: tuple[str,...] = (OPENING_HAND_BATTLEFIELD_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','permission','requires_complete_card_program'},
            field='opening-hand action permission')
        if descriptor['requires_complete_card_program'] is not True:
            raise SemanticNodeError('Opening-hand entry requires complete-card admission')
        return StaticActionPermissionHandler.validate(self,
            {k:v for k,v in descriptor.items() if k!='requires_complete_card_program'})


@dataclass(frozen=True, slots=True)
class OpeningHandLuckPermissionHandler(OpeningHandBattlefieldPermissionHandler):
    handler_id: str = OPENING_HAND_LUCK_HANDLER_ID
    permission: ActionPermissionKind = ActionPermissionKind.OPENING_HAND_LUCK
    rule_references: tuple[str, ...] = ("103.6", "103.6a", "122.1", "609.3")
    capability_dependencies: tuple[str, ...] = (OPENING_HAND_LUCK_CAPABILITY,)


@dataclass(frozen=True,slots=True)
class OpeningHandRevealPermissionHandler(StaticActionPermissionHandler):
    handler_id: str = OPENING_REVEAL_HANDLER
    permission: ActionPermissionKind = ActionPermissionKind.OPENING_HAND_REVEAL
    rule_references: tuple[str,...] = ('103.6b','603.7a','603.7g')
    capability_dependencies: tuple[str,...] = (OPENING_REVEAL_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','permission','requires_complete_card_program','scheduled_result'},
            field='opening-hand reveal permission')
        if descriptor['requires_complete_card_program'] is not True:
            raise SemanticNodeError('Opening reveal requires complete-card admission')
        StaticActionPermissionHandler.validate(self,
            {k:v for k,v in descriptor.items() if k not in {'requires_complete_card_program','scheduled_result'}})
        try:return OpeningRevealSpec.from_dict(descriptor['scheduled_result'])
        except (ValueError,TypeError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        spec=self.validate(descriptor)
        return (StaticActionPermission(self.permission,context.source_controller,context.source_ref,
            context.source_logical_object_id,context.source_semantic_key,opening_reveal_spec=spec),)


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
    top_spell_price: LibraryTopSpellPrice | None = None

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> tuple[tuple[ObjectQuerySpec, ...], tuple[ObjectQuerySpec, ...]]:
        qualifier = self.qualifier(descriptor)
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "land_queries",
                "spell_queries",
                *({'qualifier'} if qualifier is not None else ()),
                *({'minimum_spell_mana_value'} if qualifier is LibraryTopQualifier.MINIMUM_SPELL_MANA_VALUE else ()),
            },
            field="Library-top action handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Library-top action handler ID mismatch")
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
        if self.top_spell_price is LibraryTopSpellPrice.MANA_VALUE_IN_LIFE and (
            land_queries != (ObjectQuerySpec(types_all=("land",)),)
            or spell_queries != (ObjectQuerySpec(excluded_types=("land",)),)
        ):
            raise SemanticNodeError("The library-top life price requires its complete land and spell permission")
        if qualifier is LibraryTopQualifier.CHOSEN_CREATURE_TYPE and (
            land_queries or spell_queries != (ObjectQuerySpec(types_all=('creature',), excluded_types=('land',)),)
        ):
            raise SemanticNodeError('Chosen-type library access requires its closed creature spell query')
        if qualifier is LibraryTopQualifier.ARTIFACT_OR_COLORLESS_SPELL and (
            land_queries or spell_queries != ARTIFACT_OR_COLORLESS_LIBRARY_SPELL_QUERIES
        ):
            raise SemanticNodeError('Artifact or colorless library access requires its closed spell-only query')
        if qualifier is LibraryTopQualifier.MINIMUM_SPELL_MANA_VALUE:
            amount = descriptor['minimum_spell_mana_value']
            if not spell_queries or type(amount) is not int or not 1 <= amount <= 99:
                raise SemanticNodeError('Library-top mana value requires a bounded positive spell threshold')
        if (
            len(land_queries) != len({repr(query) for query in land_queries})
            or len(spell_queries)
            != len({repr(query) for query in spell_queries})
        ):
            raise SemanticNodeError(
                "Library-top action queries must be unique"
            )
        return land_queries, spell_queries

    def qualifier(self, descriptor: Mapping[str, Any]) -> LibraryTopQualifier | None:
        version = descriptor.get('schema_version')
        if type(version) is not int or version != self.schema_version:
            raise SemanticNodeError('Unsupported library-top action schema version')
        if version == 1:
            return None
        try:
            return LibraryTopQualifier(descriptor.get('qualifier'))
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError('Unknown qualified library-top action') from exc

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
                library_top_qualifier=self.qualifier(descriptor),
                minimum_spell_mana_value=descriptor.get('minimum_spell_mana_value'),
                top_spell_price=self.top_spell_price,
            ),
        )


LIBRARY_TOP_LIFE_ACTION_HANDLER = LibraryTopActionHandler(
    handler_id=LIBRARY_TOP_LIFE_ACTION_HANDLER_ID,
    rule_references=("107.3b", "118.9", "118.9a", "202.3", "305.2", "401.5", "601.2b", "601.2f", "601.2h"),
    capability_dependencies=("library.action.top.static", LIBRARY_TOP_LIFE_ACTION_CAPABILITY),
    top_spell_price=LibraryTopSpellPrice.MANA_VALUE_IN_LIFE,
)


_QUALIFIED_LIBRARY_TOP_HANDLER = LibraryTopActionHandler(handler_id=QUALIFIED_LIBRARY_TOP_HANDLER_ID, schema_version=2,
    capability_dependencies=('library.action.top.static', QUALIFIED_LIBRARY_TOP_CAPABILITY))


def qualified_library_top_runtime_dependencies(program: Any) -> tuple[str, ...]:
    if program.event != ACTION_PERMISSION_EVENT:
        return ()
    required = set()
    handler = _QUALIFIED_LIBRARY_TOP_HANDLER
    for descriptor in program.handlers:
        if descriptor.get('handler_id') != handler.handler_id:
            continue
        handler.validate(descriptor)
        if program.active_zone != 'battlefield' or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage:
            raise SemanticNodeError('Qualified library access requires its current battlefield ability')
        required.update(library_top_qualifier_capabilities(handler.qualifier(descriptor)))
    return tuple(sorted(required))


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
    scope: ActionPermissionScope = ActionPermissionScope.CONTROLLER

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
        amount = self.validate(descriptor)
        players = (
            context.active_seats
            if self.scope is ActionPermissionScope.ALL_PLAYERS
            else (context.source_controller,)
        )
        return tuple(
            StaticActionPermission(
                kind=ActionPermissionKind.ADDITIONAL_LAND_PLAY,
                player=player,
                source_ref=context.source_ref,
                source_logical_object_id=context.source_logical_object_id,
                source_semantic_key=context.source_semantic_key,
                additional_land_plays=amount,
            )
            for player in players
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
    from .library_search_rules import LibrarySearchRuleHandler, SEARCH_CONTROL_CAPABILITY, SEARCH_CONTROL_HANDLER
    from .hand_visibility import StaticHandVisibilityHandler
    from .mana_spending import (
        TAP_MANA_PRODUCTION_MULTIPLIER_CAPABILITY, mana_spending_handlers, tap_mana_multiplier_handlers,
    )
    capabilities = load_default_capability_registry()
    loyalty_handlers = ()
    if capabilities.capability(SOURCE_LOYALTY_INSTANT_CAPABILITY) is not None:
        loyalty_handlers = tuple(
            StaticActionPermissionHandler(
                handler_id=handler_id, permission=permission,
                rule_references=("101.1", "117.1b", "606.3", "611.3a"),
                capability_dependencies=(SOURCE_LOYALTY_INSTANT_CAPABILITY,),
            )
            for permission, handler_id in SOURCE_LOYALTY_INSTANT_HANDLERS.items()
        )
    registry = ActionPermissionRegistry(
        (
            LibrarySearchRuleHandler(),
            LibrarySearchRuleHandler(handler_id=SEARCH_CONTROL_HANDLER,
                family="permission.action.library-search-control",
                rule_references=("611.3b", "616.1", "701.23a", "701.23b", "701.23f", "723.4", "723.5"),
                capability_dependencies=(SEARCH_CONTROL_CAPABILITY,)),
            StaticHandVisibilityHandler(),
            *mana_spending_handlers(),
            *(tap_mana_multiplier_handlers() if capabilities.capability(TAP_MANA_PRODUCTION_MULTIPLIER_CAPABILITY) is not None else ()),
            *loyalty_handlers,
            OpeningHandBattlefieldPermissionHandler(),
            OpeningHandLuckPermissionHandler(),
            OpeningHandRevealPermissionHandler(),
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
            _QUALIFIED_LIBRARY_TOP_HANDLER,
            LIBRARY_TOP_LIFE_ACTION_HANDLER,
            AdditionalLandPlayHandler(),
            AdditionalLandPlayHandler(
                handler_id=SHARED_ADDITIONAL_LAND_PLAY_HANDLER_ID,
                capability_dependencies=(SHARED_ADDITIONAL_LAND_PLAY_CAPABILITY,),
                scope=ActionPermissionScope.ALL_PLAYERS,
            ),
        )
    )
    registry.require_registered_capabilities(capabilities)
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

    def _fixed_public_state_condition_holds(
        self, source: Any, condition: FixedPublicStateConditionSpec,
    ) -> bool: ...

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
            copiable_oracle_id(source),
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


def source_has_instant_loyalty_permission(host: ActionPermissionHost, player: str, source: Any) -> bool:
    """Check current static authority for the exact source of the loyalty ability."""
    if (getattr(host, "semantics", None) is None or source.zone != "battlefield"
            or source.phased_out or source.controller != player):
        return False
    entered = source.entered_battlefield_turn_sequence == host.state.turn_sequence
    for permission in controller_action_permissions(host, player):
        if (permission.source_ref != source.ref
                or permission.source_logical_object_id != source.logical_object_id):
            continue
        if (permission.kind is ActionPermissionKind.ACTIVATE_SOURCE_LOYALTY_AT_INSTANT
                or entered and permission.kind is ActionPermissionKind.ACTIVATE_ENTERED_SOURCE_LOYALTY_AT_INSTANT):
            return True
    return False


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


def _opponent_controls_more_lands(host: ActionPermissionHost, player: str) -> bool:
    counts = Counter(card.controller for card in host.state.cards.values()
        if card.zone == 'battlefield' and not card.phased_out
        and object_matches_query(host._public_object_query_result(card), _BATTLEFIELD_LAND))
    return any(counts[seat] > counts[player] for seat in host.active_seats if seat != player)


def _bound_library_top_queries(host: ActionPermissionHost, permission: StaticActionPermission, *, spell: bool):
    queries = permission.top_spell_queries if spell else permission.top_land_queries
    qualifier = permission.library_top_qualifier
    if not queries or qualifier in {
        None, LibraryTopQualifier.MINIMUM_SPELL_MANA_VALUE, LibraryTopQualifier.ARTIFACT_OR_COLORLESS_SPELL,
    }:
        return queries
    if qualifier is LibraryTopQualifier.OPPONENT_MORE_LANDS:
        return queries if _opponent_controls_more_lands(host, permission.player) else ()
    if qualifier is LibraryTopQualifier.DISTINCT_CREATURE_POWERS:
        from ..public_characteristic_aggregates import current_public_characteristic_aggregate

        amount = current_public_characteristic_aggregate(host, permission.player, _DISTINCT_POWER)
        return queries if amount >= 3 else ()
    source = next((card for card in host.state.cards.values()
        if card.ref == permission.source_ref and card.logical_object_id == permission.source_logical_object_id), None)
    if source is None:
        return ()
    history_condition = _LIBRARY_TOP_HISTORY_CONDITIONS.get(qualifier)
    if history_condition is not None:
        return queries if host._fixed_public_state_condition_holds(source, history_condition) else ()
    if qualifier is LibraryTopQualifier.ATTACHED_TO_CREATURE:
        from ..attachments import attached_object_identity

        attached = attached_object_identity(host.state.cards, source)
        if attached is None:
            return ()
        recipient = host._public_object_query_result(host.state.cards[attached.object_id])
        return queries if 'creature' in recipient.types else ()
    from ..rules.chosen_type_events import chosen_type_event_value

    chosen = chosen_type_event_value(source)
    return tuple(replace(query, subtypes_all=(chosen,)) for query in queries) if chosen is not None else ()


def _face_down_library_spell_query(host: ActionPermissionHost, card: Any) -> ObjectQueryResult:
    from ..morph import MORPH_CAST_METHOD, MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics

    view = replace(card, zone='stack', face_down=True, annotations={
        **card.annotations, MORPH_FACE_DOWN_ANNOTATION: face_down_characteristics(MORPH_CAST_METHOD),
    })
    return host._public_object_query_result(view)


def _matching_library_top_permissions(
    host: ActionPermissionHost,
    player: str,
    card: Any,
    *,
    spell: bool,
    face_down: bool = False,
) -> tuple[StaticActionPermission, ...]:
    if not _is_owned_library_top(host, player, card):
        return ()
    matching = []
    front_row = face_down_row = None
    for permission in controller_action_permissions(host, player):
        if permission.kind is not ActionPermissionKind.LIBRARY_TOP_ACTION:
            continue
        queries = _bound_library_top_queries(host, permission, spell=spell)
        if not queries:
            continue
        if face_down and permission.library_top_qualifier is LibraryTopQualifier.ARTIFACT_OR_COLORLESS_SPELL:
            if face_down_row is None:
                face_down_row = _face_down_library_spell_query(host, card)
            row = face_down_row
        else:
            if front_row is None:
                front_row = host._public_object_query_result(card)
            row = front_row
        if any(object_matches_query(row, query) for query in queries):
            matching.append(permission)
    return tuple(matching)


def controller_has_library_top_land_permission(host: ActionPermissionHost, player: str, card: Any) -> bool:
    return bool(_matching_library_top_permissions(host, player, card, spell=False))


def controller_life_priced_library_top_permissions(host: ActionPermissionHost, player: str, card: Any):
    return tuple(permission for permission in _matching_library_top_permissions(host, player, card, spell=True)
                 if permission.top_spell_price is LibraryTopSpellPrice.MANA_VALUE_IN_LIFE)


def controller_has_face_down_library_top_spell_permission(host: ActionPermissionHost, player: str, card: Any) -> bool:
    return any(
        permission.library_top_qualifier is LibraryTopQualifier.ARTIFACT_OR_COLORLESS_SPELL
        for permission in _matching_library_top_permissions(host, player, card, spell=True, face_down=True)
    )


def controller_library_top_spell_minimum_x(
    host: ActionPermissionHost, player: str, card: Any, *, face_down: bool = False,
) -> int | None:
    """Read the lowest announced X any matching static permission would accept."""
    matching = tuple(
        permission for permission in _matching_library_top_permissions(host, player, card, spell=True, face_down=face_down)
        if permission.top_spell_price is None
    )
    if not matching:
        return None
    threshold = min(permission.minimum_spell_mana_value or 0 for permission in matching)
    if threshold == 0:
        return 0
    if face_down:
        return None
    from ..rules.casting.face_queries import current_printed_cast_mana_cost
    from ..selection.exile_cast import mana_value_of_cost
    from ..util import parse_mana_symbols

    record = host.card_record(card)
    if record is None:
        return None
    mana_cost = current_printed_cast_mana_cost(host, card, record)
    missing = threshold - mana_value_of_cost(mana_cost)
    if missing <= 0:
        return 0
    symbols = parse_mana_symbols(mana_cost).count('X')
    return ceil(missing / symbols) if symbols else None


def controller_has_library_top_spell_permission(host: ActionPermissionHost, player: str, card: Any) -> bool:
    return (controller_library_top_spell_minimum_x(host, player, card) is not None
            or bool(controller_life_priced_library_top_permissions(host, player, card)))


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
LAND_PLAY_ACCOUNTING_STAT = "land_play_accounting_v2"
LAND_PLAY_ACCOUNTING_VERSION = 2


def _legacy_used_additional_land_play_slots(
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
    return tuple(dict.fromkeys(used))


def land_play_accounting(
    host: ActionPermissionHost,
    player: str,
) -> tuple[int, int]:
    """Return the turn's fixed base allowance and total completed plays.

    The versioned record is authoritative once written. Older Game Record v3
    checkpoints stored only the unused base allowance plus source-specific
    additional slots. Their state is adapted explicitly: a missing base unit
    means one base play occurred, while every recorded legacy slot represents
    another completed play. This preserves historical replay without treating
    a newly incarnated source as a new quota.
    """

    state = host.state.players[player]
    raw = state.stats.get(LAND_PLAY_ACCOUNTING_STAT)
    if raw is not None:
        if not isinstance(raw, Mapping) or set(raw) != {
            "version",
            "base_allowance",
            "played",
        }:
            raise SemanticNodeError(
                "Land-play accounting must be a closed versioned object"
            )
        version = raw["version"]
        base_allowance = raw["base_allowance"]
        played = raw["played"]
        if (
            type(version) is not int
            or version != LAND_PLAY_ACCOUNTING_VERSION
            or type(base_allowance) is not int
            or base_allowance < 0
            or type(played) is not int
            or played < 0
        ):
            raise SemanticNodeError(
                "Land-play accounting values are malformed"
            )
        return base_allowance, played

    remaining = state.land_plays_remaining
    if type(remaining) is not int or remaining < 0:
        raise SemanticNodeError(
            "Legacy land plays remaining must be a nonnegative integer"
        )
    base_played = 0 if remaining else 1
    base_allowance = remaining + base_played
    played = base_played + len(
        _legacy_used_additional_land_play_slots(host, player)
    )
    return base_allowance, played


def land_play_quota(
    host: ActionPermissionHost,
    player: str,
) -> tuple[int, int, int]:
    base_allowance, played = land_play_accounting(host, player)
    additional_allowance = len(
        additional_land_play_permission_slots(host, player)
    )
    from ..rules.land_play_permissions import additional_land_plays_this_turn

    temporary = additional_land_plays_this_turn(host.state.players[player].stats, host.state.turn_sequence)
    total_allowance = base_allowance + additional_allowance + temporary
    return total_allowance, played, max(0, total_allowance - played)


def land_play_permission_options(
    host: ActionPermissionHost,
    player: str,
) -> tuple[str, ...]:
    _allowed, _played, remaining = land_play_quota(host, player)
    return tuple(
        f"land-play-quota:{index}" for index in range(remaining)
    )


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
    "LAND_PLAY_ACCOUNTING_STAT",
    "LAND_PLAY_ACCOUNTING_VERSION",
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
    "additional_land_play_permission_slots",
    "controller_action_permissions",
    "controller_has_action_permission",
    "controller_has_library_top_land_permission",
    "controller_has_library_top_spell_permission",
    "default_action_permission_registry",
    "library_top_visibility",
    "land_play_permission_options",
    "land_play_accounting",
    "land_play_quota",
]
