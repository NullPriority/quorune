from __future__ import annotations

"""Typed battlefield-static casting and stack-counter rules."""

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..attachments import attached_player_seat
from ..card_program_faces import program_matches_face
from ..cast_timing import CastTimingPermission
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import ObjectQueryResult, object_matches_query, object_query_result
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .current_ability_components import program_has_current_ability_fragments


STATIC_CAST_RULE_EVENT = "cast.static.rule"
STATIC_CAST_TIMING_HANDLER_ID = "rule.cast.timing.fixed-query.v1"
STATIC_CAST_PROHIBITION_HANDLER_ID = "rule.cast.prohibition.fixed-public.v1"
STATIC_CAST_LIMIT_HANDLER_ID = "rule.cast.limit.once-per-turn.v1"
STATIC_UNCOUNTERABLE_HANDLER_ID = "rule.stack.uncounterable.fixed-query.v1"


class StaticCastRuleKind(StrEnum):
    TIMING_PERMISSION = "timing_permission"
    CAST_PROHIBITION = "cast_prohibition"
    CAST_LIMIT = "cast_limit"
    UNCOUNTERABLE = "uncounterable"


class StaticCastPlayerScope(StrEnum):
    SOURCE_CONTROLLER = "source_controller"
    SOURCE_OPPONENTS = "source_opponents"
    ALL_PLAYERS = "all_players"
    ENCHANTED_PLAYER = "enchanted_player"


class StaticCastCondition(StrEnum):
    ALWAYS = "always"
    COMBAT = "combat"
    SOURCE_CONTROLLER_TURN = "source_controller_turn"
    SOURCE_TAPPED = "source_tapped"
    OPPONENT_CAST_SPELL_THIS_TURN = "opponent_cast_spell_this_turn"
    OPPONENT_END_STEP = "opponent_end_step"


@dataclass(frozen=True, slots=True)
class StaticCastRuleSourceContext:
    source_ref: str
    source_logical_object_id: str
    source_controller: str
    source_tapped: bool
    chosen_name: str
    attached_player: str | None


@dataclass(frozen=True, slots=True)
class StaticCastRule:
    kind: StaticCastRuleKind
    player_scope: StaticCastPlayerScope
    spell_queries: tuple[ObjectQuerySpec, ...]
    condition: StaticCastCondition
    origin_zones: tuple[str, ...]
    maximum_per_turn: int | None
    chosen_name: bool
    affects_abilities: bool
    source_ref: str
    source_logical_object_id: str
    source_controller: str
    source_tapped: bool
    source_chosen_name: str
    source_attached_player: str | None


@dataclass(frozen=True, slots=True)
class StaticCastRuleHandler:
    handler_id: str
    kind: StaticCastRuleKind
    capability_dependencies: tuple[str, ...]
    schema_version: int = 1
    family: str = "rule.cast.static"
    event: str = STATIC_CAST_RULE_EVENT
    rule_references: tuple[str, ...] = (
        "101.2",
        "117.1a",
        "601.2",
        "601.2e",
        "601.3",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "kind",
                "player_scope",
                "spell_queries",
                "condition",
                "origin_zones",
                "maximum_per_turn",
                "chosen_name",
                "affects_abilities",
            },
            field="Static cast-rule handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Static cast-rule handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError("Unsupported static cast-rule schema version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Static cast-rule handlers must use {self.event}"
            )
        try:
            kind = StaticCastRuleKind(descriptor["kind"])
            scope = StaticCastPlayerScope(descriptor["player_scope"])
            condition = StaticCastCondition(descriptor["condition"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError("Static cast-rule enums are closed") from exc
        if kind is not self.kind:
            raise SemanticNodeError("Static cast-rule kind does not match handler")
        raw_queries = descriptor["spell_queries"]
        if not isinstance(raw_queries, list):
            raise SemanticNodeError("Static cast-rule queries must be an array")
        try:
            queries = tuple(ObjectQuerySpec.from_dict(value) for value in raw_queries)
        except (ObjectQueryError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if not queries or any("land" not in query.excluded_types for query in queries):
            raise SemanticNodeError(
                "Static cast-rule spell queries must exclude lands"
            )
        if len(queries) != len({repr(query) for query in queries}):
            raise SemanticNodeError("Static cast-rule queries must be unique")
        raw_zones = descriptor["origin_zones"]
        if not isinstance(raw_zones, list) or any(
            type(value) is not str or value not in {"graveyard", "library"}
            for value in raw_zones
        ):
            raise SemanticNodeError("Static cast-rule origin zones are closed")
        zones = tuple(sorted(set(raw_zones)))
        maximum = descriptor["maximum_per_turn"]
        if maximum is not None and (type(maximum) is not int or maximum != 1):
            raise SemanticNodeError("Static cast limits support only one per turn")
        for field_name in ("chosen_name", "affects_abilities"):
            if type(descriptor[field_name]) is not bool:
                raise SemanticNodeError(f"Static cast-rule {field_name} must be boolean")
        if kind is StaticCastRuleKind.CAST_LIMIT and maximum != 1:
            raise SemanticNodeError("Static cast-limit rules require maximum one")
        if kind is not StaticCastRuleKind.CAST_LIMIT and maximum is not None:
            raise SemanticNodeError("Only static cast-limit rules have a maximum")
        if zones and kind is not StaticCastRuleKind.CAST_PROHIBITION:
            raise SemanticNodeError("Only cast prohibitions constrain origin zones")
        if descriptor["chosen_name"] and kind is not StaticCastRuleKind.CAST_PROHIBITION:
            raise SemanticNodeError("Only cast prohibitions use a chosen name")
        if descriptor["affects_abilities"] and kind is not StaticCastRuleKind.UNCOUNTERABLE:
            raise SemanticNodeError("Only uncounterable rules may affect abilities")
        return {
            "kind": kind,
            "scope": scope,
            "queries": queries,
            "condition": condition,
            "zones": zones,
            "maximum": maximum,
            "chosen_name": descriptor["chosen_name"],
            "affects_abilities": descriptor["affects_abilities"],
        }

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: StaticCastRuleSourceContext,
    ) -> tuple[StaticCastRule, ...]:
        value = self.validate(descriptor)
        return (
            StaticCastRule(
                kind=value["kind"],
                player_scope=value["scope"],
                spell_queries=value["queries"],
                condition=value["condition"],
                origin_zones=value["zones"],
                maximum_per_turn=value["maximum"],
                chosen_name=value["chosen_name"],
                affects_abilities=value["affects_abilities"],
                source_ref=context.source_ref,
                source_logical_object_id=context.source_logical_object_id,
                source_controller=context.source_controller,
                source_tapped=context.source_tapped,
                source_chosen_name=" ".join(context.chosen_name.casefold().split()),
                source_attached_player=context.attached_player,
            ),
        )


class StaticCastRuleRegistry(
    RuntimeComponentRegistry[StaticCastRuleSourceContext, StaticCastRule]
):
    pass


@lru_cache(maxsize=1)
def default_static_cast_rule_registry() -> StaticCastRuleRegistry:
    registry = StaticCastRuleRegistry(
        (
            StaticCastRuleHandler(
                STATIC_CAST_TIMING_HANDLER_ID,
                StaticCastRuleKind.TIMING_PERMISSION,
                ("casting.timing.fixed_query_static",),
            ),
            StaticCastRuleHandler(
                STATIC_CAST_PROHIBITION_HANDLER_ID,
                StaticCastRuleKind.CAST_PROHIBITION,
                ("casting.prohibition.fixed_public",),
            ),
            StaticCastRuleHandler(
                STATIC_CAST_LIMIT_HANDLER_ID,
                StaticCastRuleKind.CAST_LIMIT,
                ("casting.limit.once_per_turn_static",),
            ),
            StaticCastRuleHandler(
                STATIC_UNCOUNTERABLE_HANDLER_ID,
                StaticCastRuleKind.UNCOUNTERABLE,
                ("stack.counter.prohibition.fixed_query_static",),
            ),
        )
    )
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


class StaticCastRuleHost(Protocol):
    state: Any
    active_seats: Sequence[str]
    semantics: Any

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> Sequence[Any]: ...

    def _effective_card_data(self, card: Any, **kwargs: Any) -> Mapping[str, Any]: ...

    def _type_parts(self, value: str) -> tuple[set[str], set[str], set[str]]: ...

    def _current_turn_history(self, kind: str) -> Sequence[Any]: ...

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def active_static_cast_rules(host: StaticCastRuleHost) -> tuple[StaticCastRule, ...]:
    registry = default_static_cast_rule_registry()
    rules: list[StaticCastRule] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.zone != "battlefield" or source.phased_out:
            continue
        record = host.card_record(source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event=STATIC_CAST_RULE_EVENT,
        ):
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
            context = StaticCastRuleSourceContext(
                source_ref=source.ref,
                source_logical_object_id=source.logical_object_id,
                source_controller=source.controller,
                source_tapped=bool(source.tapped),
                chosen_name=str(source.annotations.get("chosen_name") or ""),
                attached_player=attached_player_seat(source),
            )
            for descriptor in program.handlers:
                if registry.describe(str(descriptor.get("handler_id") or "")):
                    rules.extend(registry.lower(descriptor, context))
    return tuple(
        sorted(
            rules,
            key=lambda rule: (
                rule.kind.value,
                rule.source_ref,
                rule.source_logical_object_id,
            ),
        )
    )


def _scope_applies(rule: StaticCastRule, player: str) -> bool:
    if rule.player_scope is StaticCastPlayerScope.ALL_PLAYERS:
        return True
    if rule.player_scope is StaticCastPlayerScope.SOURCE_CONTROLLER:
        return player == rule.source_controller
    if rule.player_scope is StaticCastPlayerScope.ENCHANTED_PLAYER:
        return player == rule.source_attached_player
    return player != rule.source_controller


def _condition_applies(host: StaticCastRuleHost, rule: StaticCastRule) -> bool:
    if rule.condition is StaticCastCondition.ALWAYS:
        return True
    if rule.condition is StaticCastCondition.COMBAT:
        return host.state.phase == "combat"
    if rule.condition is StaticCastCondition.SOURCE_CONTROLLER_TURN:
        return host.state.active_player == rule.source_controller
    if rule.condition is StaticCastCondition.SOURCE_TAPPED:
        return rule.source_tapped
    if rule.condition is StaticCastCondition.OPPONENT_END_STEP:
        return bool(
            host.state.step == "end_step"
            and host.state.active_player != rule.source_controller
        )
    return any(
        event.actor in host.active_seats
        and event.actor != rule.source_controller
        for event in host._current_turn_history("spell_cast")
    )


def spell_query_result(
    host: StaticCastRuleHost,
    card: Any,
    *,
    face: Mapping[str, Any] | None = None,
) -> ObjectQueryResult:
    effective = dict(host._effective_card_data(card))
    if face is not None:
        for key in ("name", "type_line", "colors", "keywords", "mana_value"):
            if face.get(key) is not None:
                effective[key] = face[key]
    return object_query_result(
        card,
        effective,
        type_parts=host._type_parts(str(effective.get("type_line") or "")),
        known_to_actor=True,
        attached_to_ref=None,
    )


def _rule_matches_spell(
    rule: StaticCastRule,
    row: ObjectQueryResult,
    *,
    spell_name: str,
) -> bool:
    if rule.chosen_name and (
        not rule.source_chosen_name
        or " ".join(spell_name.casefold().split()) != rule.source_chosen_name
    ):
        return False
    return any(object_matches_query(row, query) for query in rule.spell_queries)


def static_cast_timing_permissions(
    host: StaticCastRuleHost,
    player: str,
    card: Any,
    *,
    face: Mapping[str, Any] | None = None,
) -> tuple[CastTimingPermission, ...]:
    rules = tuple(
        rule
        for rule in active_static_cast_rules(host)
        if rule.kind is StaticCastRuleKind.TIMING_PERMISSION
        and _scope_applies(rule, player)
        and _condition_applies(host, rule)
    )
    if not rules:
        return ()
    row = spell_query_result(host, card, face=face)
    name = str((face or {}).get("name") or row.printed_name)
    return (
        (CastTimingPermission(),)
        if any(_rule_matches_spell(rule, row, spell_name=name) for rule in rules)
        else ()
    )


def static_cast_prohibition_reason(
    host: StaticCastRuleHost,
    player: str,
    card: Any,
    *,
    face: Mapping[str, Any] | None = None,
) -> str | None:
    rules = tuple(
        rule
        for rule in active_static_cast_rules(host)
        if rule.kind in {
            StaticCastRuleKind.CAST_PROHIBITION,
            StaticCastRuleKind.CAST_LIMIT,
        }
        and _scope_applies(rule, player)
        and _condition_applies(host, rule)
    )
    if not rules:
        return None
    row = spell_query_result(host, card, face=face)
    name = str((face or {}).get("name") or row.printed_name)
    for rule in rules:
        if not _rule_matches_spell(rule, row, spell_name=name):
            continue
        if rule.origin_zones and card.zone not in rule.origin_zones:
            continue
        if rule.kind is StaticCastRuleKind.CAST_LIMIT:
            prior_casts = sum(
                getattr(event, "actor", None) == player
                and _history_event_matches_query(event, rule.spell_queries)
                for event in host._current_turn_history("spell_cast")
            )
            if prior_casts < 1:
                continue
        return (
            "static_cast_limit"
            if rule.kind is StaticCastRuleKind.CAST_LIMIT
            else "static_cast_prohibition"
        )
    return None


def _history_event_matches_query(
    event: Any,
    queries: tuple[ObjectQuerySpec, ...],
) -> bool:
    types = {str(value).casefold() for value in getattr(event, "types", ())}
    return any(
        set(query.types_all) <= types
        and (not query.types_any or bool(set(query.types_any).intersection(types)))
        and not set(query.excluded_types).intersection(types)
        and not query.subtypes_all
        and not query.subtypes_any
        and not query.excluded_subtypes
        for query in queries
    )


def static_stack_item_uncounterable(
    host: StaticCastRuleHost,
    item: Any,
) -> bool:
    rules = tuple(
        rule
        for rule in active_static_cast_rules(host)
        if rule.kind is StaticCastRuleKind.UNCOUNTERABLE
        and _scope_applies(rule, item.controller)
        and _condition_applies(host, rule)
    )
    if not rules:
        return False
    if item.kind not in {"spell", "spell_copy"}:
        return any(rule.affects_abilities for rule in rules)
    if not item.card_object_id or item.card_object_id not in host.state.cards:
        return False
    card = host.state.cards[item.card_object_id]
    row = spell_query_result(host, card)
    return any(
        _rule_matches_spell(rule, row, spell_name=str(row.printed_name))
        for rule in rules
    )


__all__ = [
    "STATIC_CAST_LIMIT_HANDLER_ID",
    "STATIC_CAST_PROHIBITION_HANDLER_ID",
    "STATIC_CAST_RULE_EVENT",
    "STATIC_CAST_TIMING_HANDLER_ID",
    "STATIC_UNCOUNTERABLE_HANDLER_ID",
    "StaticCastCondition",
    "StaticCastPlayerScope",
    "StaticCastRule",
    "StaticCastRuleHandler",
    "StaticCastRuleKind",
    "active_static_cast_rules",
    "default_static_cast_rule_registry",
    "spell_query_result",
    "static_cast_prohibition_reason",
    "static_cast_timing_permissions",
    "static_stack_item_uncounterable",
]
