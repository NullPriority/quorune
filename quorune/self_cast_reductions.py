from __future__ import annotations

"""Typed public quantities for one spell's source-pinned cost reduction."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Protocol

from .counter_names import CounterStateError, normalized_counter_name
from .drawing import drawn_this_turn
from .object_predicate import ObjectQueryError, ObjectQuerySpec
from .object_query import object_matches_query, object_query_result


class SelfCastReductionError(ValueError):
    """A self spell-cost reduction descriptor is malformed or unsupported."""


class CastReductionQueryScope(str, Enum):
    CONTROLLER_ZONE = "controller_zone"
    ANY_OPPONENT = "any_opponent"
    OPPONENTS_COMBINED = "opponents_combined"
    # Historical v1 descriptors flattened every opponent into one query. Keep
    # decoding that value with its original combined meaning for exact replay.
    OPPONENT_ZONES = "opponent_zones"
    ALL_ZONES = "all_zones"


class CastReductionMetricKind(str, Enum):
    FIXED_PUBLIC_THRESHOLD = "fixed_public_threshold"
    OBJECT_COUNT = "object_count"
    TOTAL_MANA_VALUE = "total_mana_value"
    DEVOTION = "devotion"
    DOMAIN = "domain"
    TURN_FACT = "turn_fact"
    TURN_VALUE = "turn_value"
    SOURCE_COUNTER_COUNT = "source_counter_count"
    PARTY_SIZE = "party_size"
    PERMANENT_COLOR_COUNT = "permanent_color_count"
    DISTINCT_NAME_COUNT = "distinct_name_count"
    LIFE_DIFFERENCE = "life_difference"
    CONTROLLER_DRAW_COUNT = "controller_draw_count"
    OPPONENT_COLORLESS_LAND_COUNT = "opponent_colorless_land_count"
    OPPONENT_COUNT_DIFFERENCE = "opponent_count_difference"
    OPPONENT_HAND_COUNT = "opponent_hand_count"


class CastReductionTurnFact(str, Enum):
    CREATURE_DIED = "creature_died"
    CONTROLLER_CAST_ANOTHER_SPELL = "controller_cast_another_spell"
    OPPONENT_CAST_TWO_SPELLS = "opponent_cast_two_spells"
    CONTROLLER_TURN = "controller_turn"
    CONTROLLER_ATTACKED = "controller_attacked"
    CONTROLLER_SACRIFICED_PERMANENT = "controller_sacrificed_permanent"
    CONTROLLER_CAST_INSTANT_OR_SORCERY = (
        "controller_cast_instant_or_sorcery"
    )
    CONTROLLER_DISCARDED_COUNT = "controller_discarded_count"
    CONTROLLER_LIFE_GAINED_AMOUNT = "controller_life_gained_amount"
    CONTROLLER_ATTACKED_CREATURE_COUNT = "controller_attacked_creature_count"
    CONTROLLER_SACRIFICED_PERMANENT_COUNT = (
        "controller_sacrificed_permanent_count"
    )
    CONTROLLER_OTHER_SPELL_CAST_COUNT = "controller_other_spell_cast_count"
    CONTROLLER_COMMANDER_CAST_COUNT = "controller_commander_cast_count"
    OPPONENT_LIFE_LOST_AMOUNT = "opponent_life_lost_amount"


_MANA_KEYS = frozenset({"W", "U", "B", "R", "G", "C", "GENERIC"})
_PUBLIC_QUERY_ZONES = frozenset({"battlefield", "graveyard", "exile"})
_BASIC_LAND_TYPES = frozenset({"plains", "island", "swamp", "mountain", "forest"})


def _mana_pairs(value: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise SelfCastReductionError("Self cost reductions require a mana object")
    result: dict[str, int] = {}
    for raw_key, raw_amount in value.items():
        key = str(raw_key).upper()
        if key not in _MANA_KEYS or type(raw_amount) is not int or raw_amount <= 0:
            raise SelfCastReductionError(
                "Self cost reductions require positive canonical mana amounts"
            )
        result[key] = raw_amount
    if not result:
        raise SelfCastReductionError("Self cost reductions cannot be empty")
    return tuple(sorted(result.items()))


@dataclass(frozen=True, slots=True)
class CastReductionObjectQuery:
    scope: CastReductionQueryScope
    query: ObjectQuerySpec
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise SelfCastReductionError("Unsupported cast-reduction query version")
        if not isinstance(self.scope, CastReductionQueryScope):
            raise SelfCastReductionError("Unsupported cast-reduction query scope")
        if not isinstance(self.query, ObjectQuerySpec):
            raise SelfCastReductionError("Cast reductions require a typed object query")
        query = self.query
        if (
            len(query.zones) != 1
            or query.zones[0] not in _PUBLIC_QUERY_ZONES
            or query.owner is not None
            or query.controller is not None
            or query.excluded_controllers
            or query.include_phased_out
            or query.known_to_actor is not None
            or query.exclude_ref is not None
        ):
            raise SelfCastReductionError(
                "Cast reductions require one closed public-zone query"
            )
        if (
            self.scope is not CastReductionQueryScope.CONTROLLER_ZONE
            and query.zones[0] == "graveyard"
            and query.state_predicate is not None
        ):
            raise SelfCastReductionError(
                "Graveyard cast-reduction queries cannot carry permanent state"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope.value,
            "query": self.query.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CastReductionObjectQuery":
        expected = {"schema_version", "scope", "query"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SelfCastReductionError("Cast-reduction queries have a closed schema")
        try:
            return cls(
                schema_version=value["schema_version"],
                scope=CastReductionQueryScope(value["scope"]),
                query=ObjectQuerySpec.from_dict(value["query"]),
            )
        except (TypeError, ValueError, ObjectQueryError) as exc:
            raise SelfCastReductionError(
                "Cast-reduction query vocabulary is unsupported"
            ) from exc


@dataclass(frozen=True, slots=True)
class CastReductionMetric:
    kind: CastReductionMetricKind
    queries: tuple[CastReductionObjectQuery, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    require_all: bool = False
    color: str | None = None
    turn_fact: CastReductionTurnFact | None = None
    counter_name: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise SelfCastReductionError("Unsupported cast-reduction metric version")
        if not isinstance(self.kind, CastReductionMetricKind):
            raise SelfCastReductionError("Unsupported cast-reduction metric kind")
        if not isinstance(self.queries, tuple) or not all(
            isinstance(value, CastReductionObjectQuery) for value in self.queries
        ):
            raise SelfCastReductionError("Cast-reduction metrics require typed queries")
        if type(self.require_all) is not bool:
            raise SelfCastReductionError("Cast-reduction require_all must be boolean")
        for field_name in ("minimum", "maximum"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise SelfCastReductionError(
                    f"Cast-reduction {field_name} must be nonnegative or null"
                )
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise SelfCastReductionError("Cast-reduction bounds are contradictory")

        any_opponent = any(
            query.scope is CastReductionQueryScope.ANY_OPPONENT
            for query in self.queries
        )
        if any_opponent and self.kind not in {
            CastReductionMetricKind.FIXED_PUBLIC_THRESHOLD,
            CastReductionMetricKind.OPPONENT_COUNT_DIFFERENCE,
        }:
            raise SelfCastReductionError(
                "Any-opponent reductions require one independent threshold"
            )
        if (
            any_opponent
            and self.kind is CastReductionMetricKind.FIXED_PUBLIC_THRESHOLD
            and (len(self.queries) != 1 or self.require_all)
        ):
            raise SelfCastReductionError(
                "Any-opponent reductions require one independent threshold"
            )

        if self.kind is CastReductionMetricKind.FIXED_PUBLIC_THRESHOLD:
            if not self.queries or (self.minimum is None and self.maximum is None):
                raise SelfCastReductionError(
                    "Fixed public reductions require queries and a bound"
                )
            if any(
                value is not None
                for value in (self.color, self.turn_fact, self.counter_name)
            ):
                raise SelfCastReductionError("Fixed public reductions carry only queries")
            return
        if self.kind in {
            CastReductionMetricKind.OBJECT_COUNT,
            CastReductionMetricKind.TOTAL_MANA_VALUE,
        }:
            if not self.queries or any(
                value is not None
                for value in (
                    self.minimum,
                    self.maximum,
                    self.color,
                    self.turn_fact,
                    self.counter_name,
                )
            ) or self.require_all:
                raise SelfCastReductionError(
                    "Counted cast reductions carry only additive public queries"
                )
            return
        if self.kind is CastReductionMetricKind.DEVOTION:
            if (
                self.queries
                or self.minimum is not None
                or self.maximum is not None
                or self.require_all
                or self.turn_fact is not None
                or self.counter_name is not None
                or self.color not in set("WUBRG")
            ):
                raise SelfCastReductionError("Devotion reductions require one color")
            return
        if self.kind is CastReductionMetricKind.DOMAIN:
            if any(
                (
                    self.queries,
                    self.minimum is not None,
                    self.maximum is not None,
                    self.require_all,
                    self.color is not None,
                    self.turn_fact is not None,
                    self.counter_name is not None,
                )
            ):
                raise SelfCastReductionError("Domain reductions carry no open fields")
            return
        if self.kind in {
            CastReductionMetricKind.TURN_FACT,
            CastReductionMetricKind.TURN_VALUE,
        }:
            if (
                not isinstance(self.turn_fact, CastReductionTurnFact)
                or self.queries
                or self.minimum is not None
                or self.maximum is not None
                or self.require_all
                or self.color is not None
                or self.counter_name is not None
            ):
                raise SelfCastReductionError(
                    "Turn reductions require one closed public fact"
                )
            return
        if self.kind is CastReductionMetricKind.SOURCE_COUNTER_COUNT:
            if (
                self.schema_version != 2
                or type(self.counter_name) is not str
                or self.queries
                or self.minimum is not None
                or self.maximum is not None
                or self.require_all
                or self.color is not None
                or self.turn_fact is not None
            ):
                raise SelfCastReductionError(
                    "Source-counter reductions require one counter name"
                )
            try:
                canonical = normalized_counter_name(self.counter_name)
            except CounterStateError as exc:
                raise SelfCastReductionError(str(exc)) from exc
            object.__setattr__(self, "counter_name", canonical)
            return
        if self.kind in {
            CastReductionMetricKind.PARTY_SIZE,
            CastReductionMetricKind.PERMANENT_COLOR_COUNT,
            CastReductionMetricKind.LIFE_DIFFERENCE,
            CastReductionMetricKind.OPPONENT_COLORLESS_LAND_COUNT,
        }:
            if any(
                (
                    self.queries,
                    self.minimum is not None,
                    self.maximum is not None,
                    self.require_all,
                    self.color is not None,
                    self.turn_fact is not None,
                    self.counter_name is not None,
                )
            ):
                raise SelfCastReductionError(
                    "Closed public-value reductions carry no open fields"
                )
            return
        if self.kind is CastReductionMetricKind.CONTROLLER_DRAW_COUNT:
            if (
                self.queries
                or (self.minimum is None and self.maximum is None)
                or self.require_all
                or self.color is not None
                or self.turn_fact is not None
                or self.counter_name is not None
            ):
                raise SelfCastReductionError(
                    "Controller-draw reductions require only a public bound"
                )
            return
        if self.kind is CastReductionMetricKind.DISTINCT_NAME_COUNT:
            if (
                len(self.queries) != 1
                or self.minimum is not None
                or self.maximum is not None
                or self.require_all
                or self.color is not None
                or self.turn_fact is not None
                or self.counter_name is not None
            ):
                raise SelfCastReductionError(
                    "Distinct-name reductions require one public query"
                )
            return
        if self.kind is CastReductionMetricKind.OPPONENT_COUNT_DIFFERENCE:
            if (
                len(self.queries) != 2
                or self.minimum is None
                or self.maximum is not None
                or self.require_all
                or self.color is not None
                or self.turn_fact is not None
                or self.counter_name is not None
                or self.queries[0].scope is not CastReductionQueryScope.ANY_OPPONENT
                or self.queries[1].scope
                is not CastReductionQueryScope.CONTROLLER_ZONE
            ):
                raise SelfCastReductionError(
                    "Opponent-difference reductions require two ordered queries"
                )
            return
        if self.kind is CastReductionMetricKind.OPPONENT_HAND_COUNT:
            if (
                self.queries
                or (self.minimum is None and self.maximum is None)
                or self.require_all
                or self.color is not None
                or self.turn_fact is not None
                or self.counter_name is not None
            ):
                raise SelfCastReductionError(
                    "Opponent-hand reductions require only a public bound"
                )
            return
        raise SelfCastReductionError("Unsupported cast-reduction metric shape")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "queries": [value.to_dict() for value in self.queries],
            "minimum": self.minimum,
            "maximum": self.maximum,
            "require_all": self.require_all,
            "color": self.color,
            "turn_fact": self.turn_fact.value if self.turn_fact is not None else None,
        }
        if self.schema_version >= 2:
            result["counter_name"] = self.counter_name
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CastReductionMetric":
        expected = {
            "schema_version",
            "kind",
            "queries",
            "minimum",
            "maximum",
            "require_all",
            "color",
            "turn_fact",
        }
        extended = {*expected, "counter_name"}
        if not isinstance(value, Mapping) or set(value) not in (expected, extended):
            raise SelfCastReductionError("Cast-reduction metrics have a closed schema")
        if not isinstance(value["queries"], list):
            raise SelfCastReductionError("Cast-reduction queries must be an array")
        try:
            return cls(
                schema_version=value["schema_version"],
                kind=CastReductionMetricKind(value["kind"]),
                queries=tuple(
                    CastReductionObjectQuery.from_dict(item)
                    for item in value["queries"]
                ),
                minimum=value["minimum"],
                maximum=value["maximum"],
                require_all=value["require_all"],
                color=value["color"],
                turn_fact=(
                    CastReductionTurnFact(value["turn_fact"])
                    if value["turn_fact"] is not None
                    else None
                ),
                counter_name=value.get("counter_name"),
            )
        except (TypeError, ValueError) as exc:
            raise SelfCastReductionError(
                "Cast-reduction metric vocabulary is unsupported"
            ) from exc


@dataclass(frozen=True, slots=True)
class SelfSpellCostReductionTerm:
    reduction: tuple[tuple[str, int], ...]
    metric: CastReductionMetric
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise SelfCastReductionError("Unsupported self-reduction term version")
        object.__setattr__(self, "reduction", _mana_pairs(dict(self.reduction)))
        if not isinstance(self.metric, CastReductionMetric):
            raise SelfCastReductionError("Self-reduction terms require one typed metric")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "reduction": dict(self.reduction),
            "metric": self.metric.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SelfSpellCostReductionTerm":
        expected = {"schema_version", "reduction", "metric"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SelfCastReductionError("Self-reduction terms have a closed schema")
        return cls(
            schema_version=value["schema_version"],
            reduction=_mana_pairs(value["reduction"]),
            metric=CastReductionMetric.from_dict(value["metric"]),
        )


@dataclass(frozen=True, slots=True)
class SelfSpellCostReductionSpec:
    terms: tuple[SelfSpellCostReductionTerm, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise SelfCastReductionError("Unsupported self-reduction version")
        if not self.terms or not isinstance(self.terms, tuple) or not all(
            isinstance(value, SelfSpellCostReductionTerm) for value in self.terms
        ):
            raise SelfCastReductionError("Self reductions require one or more typed terms")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "terms": [value.to_dict() for value in self.terms],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SelfSpellCostReductionSpec":
        expected = {"schema_version", "terms"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SelfCastReductionError("Self reductions have a closed schema")
        if not isinstance(value["terms"], list):
            raise SelfCastReductionError("Self-reduction terms must be an array")
        return cls(
            schema_version=value["schema_version"],
            terms=tuple(
                SelfSpellCostReductionTerm.from_dict(item)
                for item in value["terms"]
            ),
        )


class SelfCastReductionHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...

    def _current_turn_history(self, kind: str) -> tuple[Any, ...]: ...


def _object_ids(host: SelfCastReductionHost, seat: str, query: CastReductionObjectQuery) -> tuple[str, ...]:
    zone = query.query.zones[0]
    if query.scope is CastReductionQueryScope.CONTROLLER_ZONE:
        return tuple(host.state.players[seat].zones[zone])
    if query.scope in {
        CastReductionQueryScope.ANY_OPPONENT,
        CastReductionQueryScope.OPPONENTS_COMBINED,
        CastReductionQueryScope.OPPONENT_ZONES,
    }:
        return tuple(
            object_id
            for other, player in host.state.players.items()
            if other != seat and player.in_game
            for object_id in player.zones[zone]
        )
    return tuple(
        object_id
        for player in host.state.players.values()
        if player.in_game
        for object_id in player.zones[zone]
    )


def _matching_cards_from_ids(
    host: SelfCastReductionHost,
    specification: CastReductionObjectQuery,
    object_ids: tuple[str, ...],
) -> tuple[tuple[Any, Mapping[str, Any]], ...]:
    result: list[tuple[Any, Mapping[str, Any]]] = []
    for object_id in object_ids:
        card = host.state.cards.get(object_id)
        if card is None:
            continue
        effective = host._effective_card_data(card)
        attached = host.state.cards.get(card.attached_to or "")
        row = object_query_result(
            card,
            effective,
            type_parts=host._type_parts(str(effective.get("type_line") or "")),
            known_to_actor=True,
            attached_to_ref=attached.ref if attached is not None else None,
        )
        if object_matches_query(row, specification.query):
            result.append((card, effective))
    return tuple(result)


def _matching_cards(host: SelfCastReductionHost, seat: str, specification: CastReductionObjectQuery) -> tuple[tuple[Any, Mapping[str, Any]], ...]:
    return _matching_cards_from_ids(
        host,
        specification,
        _object_ids(host, seat, specification),
    )


def _any_opponent_query_counts(
    host: SelfCastReductionHost,
    seat: str,
    query: CastReductionObjectQuery,
) -> tuple[int, ...]:
    zone = query.query.zones[0]
    return tuple(
        len(
            _matching_cards_from_ids(
                host,
                query,
                tuple(player.zones[zone]),
            )
        )
        for other, player in host.state.players.items()
        if other != seat and player.in_game
    )


def _query_count(host: SelfCastReductionHost, seat: str, metric: CastReductionMetric) -> int:
    return sum(len(_matching_cards(host, seat, query)) for query in metric.queries)


def _turn_fact_holds(host: SelfCastReductionHost, seat: str, fact: CastReductionTurnFact) -> bool:
    if fact is CastReductionTurnFact.CONTROLLER_TURN:
        return host.state.active_player == seat
    if fact is CastReductionTurnFact.CREATURE_DIED:
        return bool(host._current_turn_history("creature_died"))
    if fact is CastReductionTurnFact.CONTROLLER_ATTACKED:
        return any(
            event.actor == seat
            for event in host._current_turn_history("creature_attacked")
        )
    if fact is CastReductionTurnFact.CONTROLLER_SACRIFICED_PERMANENT:
        return any(
            event.actor == seat
            for event in host._current_turn_history("permanent_sacrificed")
        )
    if fact is CastReductionTurnFact.CONTROLLER_CAST_INSTANT_OR_SORCERY:
        return any(
            event.actor == seat
            and bool({"instant", "sorcery"}.intersection(event.types))
            for event in host._current_turn_history("spell_cast")
        )
    casts = host._current_turn_history("spell_cast")
    if fact is CastReductionTurnFact.CONTROLLER_CAST_ANOTHER_SPELL:
        return any(event.actor == seat for event in casts)
    counts: dict[str, int] = {}
    for event in casts:
        if event.actor is None or event.actor == seat:
            continue
        counts[event.actor] = counts.get(event.actor, 0) + 1
    return any(value >= 2 for value in counts.values())


def _turn_value(
    host: SelfCastReductionHost,
    seat: str,
    fact: CastReductionTurnFact,
) -> int:
    if fact is CastReductionTurnFact.CONTROLLER_DISCARDED_COUNT:
        return sum(
            event.actor == seat for event in host._current_turn_history("card_discarded")
        )
    if fact is CastReductionTurnFact.CONTROLLER_LIFE_GAINED_AMOUNT:
        return sum(
            int(event.amount)
            for event in host._current_turn_history("player_gained_life")
            if event.target == seat
        )
    if fact is CastReductionTurnFact.CONTROLLER_ATTACKED_CREATURE_COUNT:
        return sum(
            event.actor == seat
            for event in host._current_turn_history("creature_attacked")
        )
    if fact is CastReductionTurnFact.CONTROLLER_SACRIFICED_PERMANENT_COUNT:
        return sum(
            event.actor == seat
            for event in host._current_turn_history("permanent_sacrificed")
        )
    if fact is CastReductionTurnFact.CONTROLLER_OTHER_SPELL_CAST_COUNT:
        return sum(
            event.actor == seat for event in host._current_turn_history("spell_cast")
        )
    if fact is CastReductionTurnFact.CONTROLLER_COMMANDER_CAST_COUNT:
        return sum(int(value) for value in host.state.players[seat].commander_casts.values())
    if fact is CastReductionTurnFact.OPPONENT_LIFE_LOST_AMOUNT:
        return sum(
            int(event.amount)
            for event in host._current_turn_history("player_lost_life")
            if event.target != seat
        )
    raise SelfCastReductionError("Unsupported cast-reduction turn value")


def _devotion(host: SelfCastReductionHost, seat: str, color: str) -> int:
    amount = 0
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards.get(object_id)
        if card is None or card.controller != seat or card.phased_out:
            continue
        mana_cost = str(host._effective_card_data(card).get("mana_cost") or "")
        for symbol in re.findall(r"\{([^}]+)\}", mana_cost.upper()):
            if color in symbol.split("/"):
                amount += 1
    return amount


def _domain(host: SelfCastReductionHost, seat: str) -> int:
    present: set[str] = set()
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards.get(object_id)
        if card is None or card.controller != seat or card.phased_out:
            continue
        effective = host._effective_card_data(card)
        types, subtypes, _supertypes = host._type_parts(
            str(effective.get("type_line") or "")
        )
        if "land" in types:
            present.update(_BASIC_LAND_TYPES.intersection(subtypes))
    return len(present)


def cast_reduction_multiplier(
    host: SelfCastReductionHost,
    seat: str,
    metric: CastReductionMetric,
    *,
    source: Any | None = None,
) -> int:
    """Return the deterministic nonnegative multiplier for one reduction term."""

    if metric.kind is CastReductionMetricKind.OBJECT_COUNT:
        return _query_count(host, seat, metric)
    if metric.kind is CastReductionMetricKind.TOTAL_MANA_VALUE:
        total = 0
        for query in metric.queries:
            for _card, effective in _matching_cards(host, seat, query):
                value = float(effective.get("mana_value") or 0)
                if value < 0 or not value.is_integer():
                    raise SelfCastReductionError(
                        "Cast-reduction mana values must be nonnegative integers"
                    )
                total += int(value)
        return total
    if metric.kind is CastReductionMetricKind.DEVOTION:
        assert metric.color is not None
        return _devotion(host, seat, metric.color)
    if metric.kind is CastReductionMetricKind.DOMAIN:
        return _domain(host, seat)
    if metric.kind is CastReductionMetricKind.TURN_FACT:
        assert metric.turn_fact is not None
        return int(_turn_fact_holds(host, seat, metric.turn_fact))
    if metric.kind is CastReductionMetricKind.TURN_VALUE:
        assert metric.turn_fact is not None
        return _turn_value(host, seat, metric.turn_fact)
    if metric.kind is CastReductionMetricKind.SOURCE_COUNTER_COUNT:
        if source is None or metric.counter_name is None:
            raise SelfCastReductionError(
                "Source-counter reduction lacks its current source"
            )
        return max(0, int(source.counters.get(metric.counter_name, 0)))
    if metric.kind is CastReductionMetricKind.PARTY_SIZE:
        roles: set[str] = set()
        party = {"cleric", "rogue", "warrior", "wizard"}
        for object_id in host.state.players[seat].zones["battlefield"]:
            card = host.state.cards.get(object_id)
            if card is None or card.controller != seat or card.phased_out:
                continue
            effective = host._effective_card_data(card)
            types, subtypes, _supertypes = host._type_parts(
                str(effective.get("type_line") or "")
            )
            if "creature" in types:
                roles.update(party.intersection(subtypes))
        return len(roles)
    if metric.kind is CastReductionMetricKind.PERMANENT_COLOR_COUNT:
        colors: set[str] = set()
        for object_id in host.state.players[seat].zones["battlefield"]:
            card = host.state.cards.get(object_id)
            if card is None or card.controller != seat or card.phased_out:
                continue
            colors.update(
                str(value).upper()
                for value in host._effective_card_data(card).get("colors", ())
                if str(value).upper() in set("WUBRG")
            )
        return len(colors)
    if metric.kind is CastReductionMetricKind.DISTINCT_NAME_COUNT:
        query = metric.queries[0]
        return len(
            {
                str(effective.get("name") or card.printed_name)
                for card, effective in _matching_cards(host, seat, query)
            }
        )
    if metric.kind is CastReductionMetricKind.LIFE_DIFFERENCE:
        return max(
            0,
            int(host.state.config.starting_life)
            - int(host.state.players[seat].life),
        )
    if metric.kind is CastReductionMetricKind.CONTROLLER_DRAW_COUNT:
        value = drawn_this_turn(host, seat)
        return int(
            (metric.minimum is None or value >= metric.minimum)
            and (metric.maximum is None or value <= metric.maximum)
        )
    if metric.kind is CastReductionMetricKind.OPPONENT_COLORLESS_LAND_COUNT:
        count = 0
        for other, player in host.state.players.items():
            if other == seat or not player.in_game:
                continue
            for object_id in player.zones["battlefield"]:
                card = host.state.cards.get(object_id)
                if card is None or card.phased_out:
                    continue
                effective = host._effective_card_data(card)
                types, _subtypes, _supertypes = host._type_parts(
                    str(effective.get("type_line") or "")
                )
                if "land" in types and "C" in {
                    str(value).upper()
                    for value in effective.get("produced_mana", ())
                }:
                    count += 1
        return count
    if metric.kind is CastReductionMetricKind.OPPONENT_COUNT_DIFFERENCE:
        opponent_query, controller_query = metric.queries
        controller_count = len(_matching_cards(host, seat, controller_query))
        return int(
            any(
                count - controller_count >= int(metric.minimum or 0)
                for count in _any_opponent_query_counts(
                    host,
                    seat,
                    opponent_query,
                )
            )
        )
    if metric.kind is CastReductionMetricKind.OPPONENT_HAND_COUNT:
        counts = tuple(
            len(player.zones["hand"])
            for other, player in host.state.players.items()
            if other != seat and player.in_game
        )
        return int(
            any(
                (metric.minimum is None or value >= metric.minimum)
                and (metric.maximum is None or value <= metric.maximum)
                for value in counts
            )
        )

    def within_bounds(value: int) -> bool:
        return (
            (metric.minimum is None or value >= metric.minimum)
            and (metric.maximum is None or value <= metric.maximum)
        )

    if (
        len(metric.queries) == 1
        and metric.queries[0].scope is CastReductionQueryScope.ANY_OPPONENT
    ):
        return int(
            any(
                within_bounds(value)
                for value in _any_opponent_query_counts(
                    host,
                    seat,
                    metric.queries[0],
                )
            )
        )

    counts = [len(_matching_cards(host, seat, query)) for query in metric.queries]
    values = counts if metric.require_all else [sum(counts)]
    return int(all(within_bounds(value) for value in values))


def evaluated_self_reduction(
    host: SelfCastReductionHost,
    seat: str,
    specification: SelfSpellCostReductionSpec,
    *,
    source: Any | None = None,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for term in specification.terms:
        multiplier = cast_reduction_multiplier(
            host,
            seat,
            term.metric,
            source=source,
        )
        for key, amount in term.reduction:
            result[key] = result.get(key, 0) + amount * multiplier
    return result


__all__ = [
    "CastReductionMetric",
    "CastReductionMetricKind",
    "CastReductionObjectQuery",
    "CastReductionQueryScope",
    "CastReductionTurnFact",
    "SelfCastReductionError",
    "SelfSpellCostReductionSpec",
    "SelfSpellCostReductionTerm",
    "cast_reduction_multiplier",
    "evaluated_self_reduction",
]
