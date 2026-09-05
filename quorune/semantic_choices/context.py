from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from ..replacement.immutable import FrozenMap
from ..drawing.restrictions import DrawPermission
from ..object_query import ObjectQueryResult

ChoiceObjectView = ObjectQueryResult


@dataclass(frozen=True, slots=True)
class ChoiceStackView:
    ref: str
    controller: str
    label: str
    semantic_key: str
    targets: tuple[str | None, ...]
    modes: tuple[str, ...]
    target_groups: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        if not isinstance(self.target_groups, FrozenMap):
            object.__setattr__(
                self,
                "target_groups",
                FrozenMap(self.target_groups),
            )


class ObjectRulesQuery(Protocol):
    def objects(
        self,
        *,
        zones: tuple[str, ...],
        owner: str | None = None,
        controller: str | None = None,
        include_phased_out: bool = False,
    ) -> tuple[ChoiceObjectView, ...]: ...

    def object(
        self,
        ref: str,
        *,
        zones: tuple[str, ...] | None = None,
    ) -> ChoiceObjectView | None: ...


class StackRulesQuery(Protocol):
    def stack_object(
        self,
        ref: str,
        *,
        exclude_ref: str | None = None,
    ) -> ChoiceStackView | None: ...

    def stack_target_schema(
        self,
        stack_ref: str,
        *,
        actor: str,
    ) -> Mapping[str, Any] | None: ...

    def validate_stack_targets(
        self,
        stack_ref: str,
        submitted: Any,
        *,
        actor: str,
        target_schema: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], Mapping[str, Any]]: ...


class CostRulesQuery(Protocol):
    def cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
    ) -> bool: ...

    def authorized_cast_options(self) -> tuple[Mapping[str, Any], ...]: ...


class SemanticChoiceQuery(
    ObjectRulesQuery,
    StackRulesQuery,
    CostRulesQuery,
    Protocol,
):
    @property
    def seats(self) -> tuple[str, ...]: ...

    @property
    def active_seats(self) -> tuple[str, ...]: ...

    def player_life(self, seat: str) -> int: ...

    def player_counter(self, seat: str, counter: str) -> int: ...

    def player_counters(self, seat: str) -> Mapping[str, int]: ...

    def library_refs(self, seat: str, *, top_first: bool) -> tuple[str, ...]: ...

    def mana_pool(self, seat: str) -> Mapping[str, int]: ...

    def canonical_card_name(self, submitted: str) -> str | None: ...

    def drawn_this_turn(self, seat: str) -> tuple[str, ...]: ...

    def opponent_cast_colors_this_turn(self, seat: str) -> tuple[str, ...]: ...

    def draw_permission(self, seat: str) -> DrawPermission: ...

    def choice_candidate_refs(self) -> tuple[str, ...]: ...

    def damage_source_candidate_refs(self) -> tuple[str, ...]: ...

    @property
    def damage_source_candidates_are_complete(self) -> bool: ...

    @property
    def turn_sequence(self) -> int: ...


@dataclass(frozen=True, slots=True)
class SnapshotSemanticChoiceQuery:
    """Immutable facts materialized by the engine for one choice operation."""

    seat_order: tuple[str, ...]
    active_order: tuple[str, ...]
    object_rows: tuple[ChoiceObjectView, ...] = ()
    stack_rows: tuple[ChoiceStackView, ...] = ()
    life_by_seat: FrozenMap = field(default_factory=FrozenMap)
    counters_by_seat: FrozenMap = field(default_factory=FrozenMap)
    libraries_by_seat: FrozenMap = field(default_factory=FrozenMap)
    mana_by_seat: FrozenMap = field(default_factory=FrozenMap)
    affordable_costs: frozenset[str] = frozenset()
    authorized_cast_option_rows: tuple[FrozenMap, ...] = ()
    canonical_names: FrozenMap = field(default_factory=FrozenMap)
    target_schemas: FrozenMap = field(default_factory=FrozenMap)
    validated_targets: FrozenMap = field(default_factory=FrozenMap)
    drawn_this_turn_by_seat: FrozenMap = field(default_factory=FrozenMap)
    opponent_cast_colors_by_seat: FrozenMap = field(default_factory=FrozenMap)
    draw_permissions_by_seat: FrozenMap = field(default_factory=FrozenMap)
    materialized_choice_candidates: tuple[str, ...] = ()
    materialized_damage_source_candidates: tuple[str, ...] | None = None
    current_turn_sequence: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "life_by_seat",
            "counters_by_seat",
            "libraries_by_seat",
            "mana_by_seat",
            "canonical_names",
            "target_schemas",
            "validated_targets",
            "drawn_this_turn_by_seat",
            "opponent_cast_colors_by_seat",
            "draw_permissions_by_seat",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, FrozenMap):
                object.__setattr__(self, field_name, FrozenMap(value))
        object.__setattr__(
            self,
            "authorized_cast_option_rows",
            tuple(
                value if isinstance(value, FrozenMap) else FrozenMap(value)
                for value in self.authorized_cast_option_rows
            ),
        )

    @property
    def seats(self) -> tuple[str, ...]:
        return self.seat_order

    @property
    def active_seats(self) -> tuple[str, ...]:
        return self.active_order

    def objects(
        self,
        *,
        zones: tuple[str, ...],
        owner: str | None = None,
        controller: str | None = None,
        include_phased_out: bool = False,
    ) -> tuple[ChoiceObjectView, ...]:
        zone_set = frozenset(zones)
        return tuple(
            row
            for row in self.object_rows
            if row.zone in zone_set
            and (owner is None or row.owner == owner)
            and (controller is None or row.controller == controller)
            and (include_phased_out or not row.phased_out)
        )

    def object(
        self,
        ref: str,
        *,
        zones: tuple[str, ...] | None = None,
    ) -> ChoiceObjectView | None:
        zone_set = frozenset(zones or ())
        return next(
            (
                row
                for row in self.object_rows
                if row.ref == ref and (not zone_set or row.zone in zone_set)
            ),
            None,
        )

    def authorized_cast_options(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.authorized_cast_option_rows)

    def stack_object(
        self,
        ref: str,
        *,
        exclude_ref: str | None = None,
    ) -> ChoiceStackView | None:
        return next(
            (
                row
                for row in self.stack_rows
                if row.ref == ref and row.ref != exclude_ref
            ),
            None,
        )

    def stack_target_schema(
        self,
        stack_ref: str,
        *,
        actor: str,
    ) -> Mapping[str, Any] | None:
        value = self.target_schemas.get(f"{actor}:{stack_ref}")
        return dict(value) if isinstance(value, Mapping) else None

    def validate_stack_targets(
        self,
        stack_ref: str,
        submitted: Any,
        *,
        actor: str,
        target_schema: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], Mapping[str, Any]]:
        key = f"{actor}:{stack_ref}"
        value = self.validated_targets.get(key)
        if not isinstance(value, Mapping):
            raise ValueError("No target validation was materialized")
        targets = value.get("targets")
        groups = value.get("groups")
        if not isinstance(targets, tuple) or not isinstance(groups, Mapping):
            raise ValueError("Materialized target validation is malformed")
        return tuple(str(target) for target in targets), dict(groups)

    @staticmethod
    def _cost_key(seat: str, requirements: Mapping[str, int]) -> str:
        body = ",".join(
            f"{key}:{int(requirements[key])}"
            for key in sorted(requirements)
            if int(requirements[key])
        )
        return f"{seat}|{body}"

    def cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
    ) -> bool:
        return self._cost_key(seat, requirements) in self.affordable_costs

    def player_life(self, seat: str) -> int:
        return int(self.life_by_seat[seat])

    def player_counter(self, seat: str, counter: str) -> int:
        counters = self.counters_by_seat.get(seat, FrozenMap())
        return int(counters.get(counter, 0)) if isinstance(counters, Mapping) else 0

    def player_counters(self, seat: str) -> Mapping[str, int]:
        counters = self.counters_by_seat.get(seat, FrozenMap())
        if not isinstance(counters, Mapping):
            return {}
        return {
            str(name): int(amount)
            for name, amount in counters.items()
            if int(amount) > 0
        }

    def library_refs(self, seat: str, *, top_first: bool) -> tuple[str, ...]:
        values = tuple(str(value) for value in self.libraries_by_seat.get(seat, ()))
        return tuple(reversed(values)) if top_first else values

    def mana_pool(self, seat: str) -> Mapping[str, int]:
        value = self.mana_by_seat.get(seat, FrozenMap())
        return {key: int(amount) for key, amount in value.items()}

    def canonical_card_name(self, submitted: str) -> str | None:
        value = self.canonical_names.get(submitted.casefold())
        return str(value) if value is not None else None

    def drawn_this_turn(self, seat: str) -> tuple[str, ...]:
        return tuple(
            str(value)
            for value in self.drawn_this_turn_by_seat.get(seat, ())
        )

    def opponent_cast_colors_this_turn(self, seat: str) -> tuple[str, ...]:
        if seat not in self.seat_order:
            raise ValueError(f"Unknown seat {seat!r}")
        return tuple(
            str(value)
            for value in self.opponent_cast_colors_by_seat.get(seat, ())
        )

    def draw_permission(self, seat: str) -> DrawPermission:
        if seat not in self.seat_order:
            raise ValueError(f"Unknown seat {seat!r}")
        value = self.draw_permissions_by_seat.get(seat)
        if not isinstance(value, Mapping):
            raise ValueError(
                f"Draw permission was not materialized for {seat}"
            )
        return DrawPermission.from_dict(value)

    def choice_candidate_refs(self) -> tuple[str, ...]:
        return self.materialized_choice_candidates

    def damage_source_candidate_refs(self) -> tuple[str, ...]:
        return tuple(self.materialized_damage_source_candidates or ())

    @property
    def damage_source_candidates_are_complete(self) -> bool:
        return self.materialized_damage_source_candidates is not None

    @property
    def turn_sequence(self) -> int:
        return self.current_turn_sequence


@dataclass(frozen=True, slots=True)
class SemanticChoiceContext:
    actor: str
    stack_ref: str
    stack_controller: str
    stack_label: str
    source_ref: str | None
    card_ref: str | None
    semantic_program_id: str
    semantic_program_version: int | None
    query: SemanticChoiceQuery
    source_logical_object_id: str | None = None
    source_object_id: str | None = None
