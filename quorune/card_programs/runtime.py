from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING, Callable, Mapping, Protocol, Sequence

from ..attachments import attached_object_identity
from ..continuous_effects import ContinuousEffect
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
    FixedPublicStateConditionSnapshot,
)
from ..continuous_effects import Layer
from ..characteristic_fragments import CharacteristicQuantitySpec
from ..object_query import ObjectQueryResult, object_matches_query
from ..semantic_runtime import (
    ContinuousEffectSourceContext,
    default_continuous_effect_component_registry,
)

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
            turn_sequence=max(
                0, int(getattr(self.state, "turn_sequence", 0))
            ),
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
) -> tuple[ContinuousEffect, ...]:
    registry = default_continuous_effect_component_registry()
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
            programs = semantics.runtime_handler_programs_for_oracle(
                source.oracle_id,
                active_zone="battlefield",
                event="characteristics.evaluate",
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
                        public_state=public_state,
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
