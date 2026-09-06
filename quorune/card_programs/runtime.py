from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING, Callable, Mapping, Protocol, Sequence

from ..attachments import attached_object_identity
from ..ability_fragments import static_component_keys
from ..continuous_effects import ContinuousEffect
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
    FixedPublicStateConditionSnapshot,
)
from ..continuous_effects import Layer
from ..characteristic_fragments import CharacteristicQuantitySpec
from ..drawing.restrictions import drawn_this_turn
from ..object_query import ObjectQueryResult, object_matches_query
from ..replacement.immutable import FrozenMap
from ..semantic_runtime import (
    ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID,
    ContinuousEffectSourceContext,
    default_continuous_effect_component_registry,
)
from ..turn_history import current_turn_history_events

if TYPE_CHECKING:
    from ..semantics import SemanticRegistry
    from ..semantics import SemanticProgram


class ContinuousRuntimeState(Protocol):
    turn_order: Sequence[str]
    players: Mapping[str, Any]
    cards: Mapping[str, Any]


@dataclass(slots=True)
class ContinuousEffectCollectionMetrics:
    collection_calls: int = 0
    battlefield_objects_inspected: int = 0
    card_program_lookups: int = 0
    descriptors_inspected: int = 0
    effects_produced: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "collection_calls": self.collection_calls,
            "battlefield_objects_inspected": self.battlefield_objects_inspected,
            "card_program_lookups": self.card_program_lookups,
            "descriptors_inspected": self.descriptors_inspected,
            "effects_produced": self.effects_produced,
        }


@dataclass(slots=True)
class _FixedPublicStateSnapshotResolver:
    state: ContinuousRuntimeState
    public_object_resolver: Callable[[Any], ObjectQueryResult] | None
    quantity_resolver: Callable[[Any, CharacteristicQuantitySpec], int] | None
    public_object_cache: dict[str, ObjectQueryResult] = field(
        default_factory=dict
    )
    quantity_cache: dict[tuple[str, CharacteristicQuantitySpec], int] = field(
        default_factory=dict
    )

    def _public_object(self, candidate: Any) -> ObjectQueryResult | None:
        if self.public_object_resolver is None:
            return None
        object_id = str(candidate.object_id)
        if object_id not in self.public_object_cache:
            self.public_object_cache[object_id] = self.public_object_resolver(
                candidate
            )
        return self.public_object_cache[object_id]

    def _query_matches(
        self,
        candidate: Any | None,
        condition: FixedPublicStateConditionSpec,
    ) -> bool | None:
        if self.public_object_resolver is None:
            return None
        if candidate is None:
            return False
        assert condition.predicate is not None
        row = self._public_object(candidate)
        assert row is not None
        return object_matches_query(row, condition.predicate)

    def _condition_quantity(
        self,
        source: Any,
        condition: FixedPublicStateConditionSpec,
    ) -> int | None:
        if self.quantity_resolver is None or condition.quantity is None:
            return None
        key = (source.object_id, condition.quantity)
        if key not in self.quantity_cache:
            self.quantity_cache[key] = self.quantity_resolver(
                source,
                condition.quantity,
            )
        return self.quantity_cache[key]

    def snapshot(
        self,
        source: Any,
        condition: FixedPublicStateConditionSpec,
    ) -> FixedPublicStateConditionSnapshot:
        controller = self.state.players[source.controller]
        turn_sequence = max(0, int(getattr(self.state, "turn_sequence", 0)))
        cast_events = tuple(
            event
            for event in current_turn_history_events(
                getattr(self.state, "turn_history", None),
                turn_sequence=turn_sequence,
                kind="spell_cast",
            )
            if event.actor == source.controller
        )
        attached = (
            self.state.cards.get(source.attached_to)
            if getattr(source, "attached_to", None)
            else None
        )
        return FixedPublicStateConditionSnapshot(
            source_controller=source.controller,
            active_player=getattr(self.state, "active_player", None),
            controller_hand_count=len(controller.zones["hand"]),
            controller_graveyard_card_count=len(
                controller.zones["graveyard"]
            ),
            controller_life=int(controller.life),
            opponent_life_totals=tuple(
                int(self.state.players[other].life)
                for other in self.state.turn_order
                if other != source.controller
                and self.state.players[other].in_game
            ),
            turn_sequence=turn_sequence,
            source_entered_battlefield_turn_sequence=max(
                0,
                int(
                    getattr(
                        source,
                        "entered_battlefield_turn_sequence",
                        0,
                    )
                ),
            ),
            source_counters=tuple(
                sorted(
                    (str(name), int(amount))
                    for name, amount in source.counters.items()
                )
            ),
            controller_draw_count=drawn_this_turn(
                self,
                source.controller,
            ),
            controller_spell_cast_count=len(cast_events),
            controller_noncreature_spell_cast_count=sum(
                "creature" not in event.types for event in cast_events
            ),
            controller_instant_sorcery_cast_count=sum(
                bool({"instant", "sorcery"}.intersection(event.types))
                for event in cast_events
            ),
            opponent_poison_counter_counts=tuple(
                max(0, int(getattr(self.state.players[other], "poison", 0)))
                for other in self.state.turn_order
                if other != source.controller
                and self.state.players[other].in_game
            ),
            controller_is_monarch=(
                getattr(self.state, "monarch", None) == source.controller
            ),
            source_query_matches=(
                self._query_matches(source, condition)
                if condition.kind
                is FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY
                else None
            ),
            attached_query_matches=(
                self._query_matches(attached, condition)
                if condition.kind
                is FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY
                else None
            ),
            condition_quantity=self._condition_quantity(source, condition),
        )


def _applicable_static_programs(
    semantics: "SemanticRegistry",
    source: Any,
    semantic_keys: Sequence[str],
) -> tuple["SemanticProgram", ...]:
    compatibility_programs = {
        program.key: program
        for program in semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event="characteristics.evaluate",
        )
    }
    programs = []
    for semantic_key in sorted(
        {value for value in semantic_keys if type(value) is str and value}
    ):
        candidate = semantics.get(semantic_key) or compatibility_programs.get(
            semantic_key
        )
        if (
            candidate is not None
            and candidate.handlers
            and candidate.active_zone == "battlefield"
            and candidate.event == "characteristics.evaluate"
            and candidate.ability_id.startswith("static:")
        ):
            programs.append(candidate)
    return tuple(programs)


def _annotated_static_component_keys(source: Any) -> tuple[str, ...]:
    annotations = getattr(source, "annotations", {}) or {}
    copy_overrides = annotations.get("copy_overrides")
    characteristics = (
        copy_overrides
        if isinstance(copy_overrides, Mapping)
        and "ability_fragments" in copy_overrides
        else annotations.get("object_characteristics")
        or annotations.get("token_characteristics")
        or {}
    )
    raw = (
        characteristics.get("ability_fragments", ())
        if isinstance(characteristics, Mapping)
        else ()
    )
    return static_component_keys(
        (
            *raw,
            *(annotations.get("granted_ability_fragments") or ()),
        )
    )


def _continuous_programs_for_source(
    semantics: "SemanticRegistry",
    source: Any,
    static_component_resolver: Callable[[Any], Sequence[str]] | None,
) -> tuple["SemanticProgram", ...]:
    if static_component_resolver is not None:
        return _applicable_static_programs(
            semantics,
            source,
            static_component_resolver(source),
        )
    copy_overrides = (getattr(source, "annotations", {}) or {}).get(
        "copy_overrides"
    )
    copied_components = (
        isinstance(copy_overrides, Mapping)
        and "ability_fragments" in copy_overrides
    )
    programs = {
        program.key: program
        for program in (
            ()
            if copied_components
            else semantics.runtime_handler_programs_for_oracle(
                source.oracle_id,
                active_zone="battlefield",
                event="characteristics.evaluate",
            )
        )
    }
    programs.update(
        {
            program.key: program
            for program in _applicable_static_programs(
                semantics,
                source,
                _annotated_static_component_keys(source),
            )
        }
    )
    return tuple(programs[key] for key in sorted(programs))


def collect_card_program_continuous_effects(
    state: ContinuousRuntimeState,
    semantics: "SemanticRegistry",
    program_is_trusted: Callable[["SemanticProgram"], bool],
    *,
    metrics: ContinuousEffectCollectionMetrics | None = None,
    maximum_layer: Layer | None = None,
    public_object_resolver: Callable[[Any], ObjectQueryResult] | None = None,
    quantity_resolver: Callable[[Any, CharacteristicQuantitySpec], int]
    | None = None,
    static_component_resolver: Callable[[Any], Sequence[str]] | None = None,
) -> tuple[ContinuousEffect, ...]:
    registry = default_continuous_effect_component_registry()
    registered_handler_ids = frozenset(
        str(row["handler_id"]) for row in registry.inventory()
    )
    if metrics is not None:
        metrics.collection_calls += 1
    effects: list[ContinuousEffect] = []
    public_state_resolver = _FixedPublicStateSnapshotResolver(
        state=state,
        public_object_resolver=public_object_resolver,
        quantity_resolver=quantity_resolver,
    )

    for seat in state.turn_order:
        player = state.players[seat]
        for object_id in list(player.zones["battlefield"]):
            if metrics is not None:
                metrics.battlefield_objects_inspected += 1
            source = state.cards[object_id]
            if (
                source.controller != seat
                or source.phased_out
                or getattr(source, "face_down", False)
            ):
                continue
            programs = _continuous_programs_for_source(
                semantics, source, static_component_resolver
            )
            if metrics is not None:
                metrics.card_program_lookups += 1
            for program in programs:
                if not program_is_trusted(program):
                    continue
                for descriptor_index, descriptor in enumerate(
                    program.handlers
                ):
                    if metrics is not None:
                        metrics.descriptors_inspected += 1
                    if str(
                        descriptor.get("handler_id") or ""
                    ) not in registered_handler_ids:
                        continue
                    public_state = None
                    is_fixed_public_state = descriptor.get("handler_id") == (
                        FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
                    )
                    if (
                        is_fixed_public_state
                        and maximum_layer is not None
                        and maximum_layer < Layer.ABILITY
                    ):
                        # This component can only emit layer-6 and layer-7c
                        # effects. Skipping its condition snapshot at the
                        # layer-5 query boundary prevents count predicates from
                        # recursively collecting the effect they gate.
                        continue
                    if is_fixed_public_state:
                        condition = FixedPublicStateConditionSpec.from_dict(
                            descriptor["source_condition"]
                        )
                        public_state = public_state_resolver.snapshot(
                            source,
                            condition,
                        )
                    resolved_quantity = None
                    modifier = descriptor.get("modifier")
                    raw_quantity = (
                        modifier.get("quantity")
                        if isinstance(modifier, Mapping)
                        else None
                    )
                    if (
                        descriptor.get("handler_id")
                        == ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID
                        and isinstance(raw_quantity, Mapping)
                        and quantity_resolver is not None
                        and (
                            maximum_layer is None
                            or maximum_layer >= Layer.POWER_TOUGHNESS
                        )
                    ):
                        resolved_quantity = quantity_resolver(
                            source,
                            CharacteristicQuantitySpec.from_dict(
                                raw_quantity
                            ),
                        )
                    context = ContinuousEffectSourceContext(
                        source_object_id=source.object_id,
                        source_ref=source.ref,
                        source_controller=source.controller,
                        source_timestamp=max(
                            0, int(source.zone_timestamp)
                        ),
                        component_id=(
                            f"{program.key}:{descriptor_index}"
                        ),
                        source_logical_object_id=source.logical_object_id,
                        source_counters=FrozenMap(
                            getattr(source, "counters", {}) or {}
                        ),
                        public_state=public_state,
                        resolved_quantity=resolved_quantity,
                        attached_object=(
                            attached_object_identity(state.cards, source)
                            if getattr(source, "attached_to", None)
                            else None
                        ),
                    )
                    lowered = registry.lower(descriptor, context)
                    if maximum_layer is not None:
                        lowered = tuple(
                            effect
                            for effect in lowered
                            if effect.layer <= maximum_layer
                        )
                    effects.extend(lowered)
                    if metrics is not None:
                        metrics.effects_produced += len(lowered)
    return tuple(
        sorted(
            effects,
            key=lambda effect: (
                int(effect.layer),
                effect.sublayer,
                effect.timestamp,
                effect.effect_id,
            ),
        )
    )
