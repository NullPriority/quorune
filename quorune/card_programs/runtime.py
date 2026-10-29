from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass, field, replace
from typing import Any, TYPE_CHECKING, Callable, Mapping, Protocol, Sequence

from ..attachments import attached_object_identity
from ..ability_fragments import static_component_keys
from ..continuous_effects import ContinuousEffect
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FIXED_PUBLIC_STATE_GRANTED_ABILITY_HANDLER_ID,
    FixedPublicStateConditionKind,
    FixedPublicStateFact,
    FixedPublicStateConditionSpec,
    FixedPublicStateConditionSnapshot,
    FixedPublicStateConditionError,
)
from ..defender_permission import CONDITIONAL_DEFENDER_PERMISSION_HANDLER
from ..as_unblocked import CONDITIONAL_AS_UNBLOCKED_HANDLER
from ..continuous_effects import Layer
from ..characteristic_fragments import CharacteristicQuantitySpec
from ..drawing.restrictions import drawn_this_turn
from ..object_query import ObjectQueryResult, object_matches_query
from ..permanent_designations import permanent_numeric_designations
from ..rules.player_speed_model import player_speed
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.context import SemanticNodeError
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
        numeric = condition.predicate.numeric_characteristic
        if numeric is not None and row.zone == 'battlefield' and not row.phased_out and 'creature' in row.types:
            from ..target_numeric import TargetNumericCharacteristic
            value = (row.effective_power if numeric.characteristic is TargetNumericCharacteristic.POWER
                else row.effective_toughness)
            if value is None:
                return None
        return object_matches_query(row, condition.predicate)

    def _condition_quantity(
        self,
        source: Any,
        condition: FixedPublicStateConditionSpec,
    ) -> int | None:
        if condition.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS:
            if self.public_object_resolver is None:
                return None
            assert condition.predicate is not None
            rows = tuple(self._public_object(self.state.cards[oid])
                for oid in self.state.players[source.controller].zones['battlefield'])
            numeric = condition.predicate.numeric_characteristic
            assert numeric is not None
            from ..target_numeric import TargetNumericCharacteristic
            if any(row is not None and not row.phased_out and 'creature' in row.types
                    and (row.effective_power if numeric.characteristic is TargetNumericCharacteristic.POWER
                        else row.effective_toughness) is None for row in rows):
                return None
            return sum(object_matches_query(row, condition.predicate) for row in rows if row is not None)
        if condition.kind is FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST:
            return self._public_fact_quantity(source, condition)
        if self.quantity_resolver is None or condition.quantity is None:
            return None
        key = (source.object_id, condition.quantity)
        if key not in self.quantity_cache:
            self.quantity_cache[key] = self.quantity_resolver(
                source,
                condition.quantity,
            )
        return self.quantity_cache[key]

    def _public_fact_quantity(
        self,
        source: Any,
        condition: FixedPublicStateConditionSpec,
    ) -> int | None:
        fact = condition.fact
        if not isinstance(fact, FixedPublicStateFact):
            return None
        if fact is FixedPublicStateFact.PERMANENT_COLOR_IS_MOST_COMMON:
            return self._most_common_color_quantity(condition)
        if source is None:
            return None
        from ..public_zone_condition_model import public_zone_fact_value
        zone_value = public_zone_fact_value(self.state, source.controller, fact.value, condition.fact_parameter)
        if zone_value is not None:
            return zone_value
        if fact is FixedPublicStateFact.CONTROLLER_CONTROLS_OWN_COMMANDER:
            return int(any(card.zone == 'battlefield' and not card.phased_out
                and card.is_commander and card.owner == source.controller and card.controller == source.controller
                for card in self.state.cards.values()))
        if fact is FixedPublicStateFact.SOURCE_RENOWNED:
            return int(source.zone == 'battlefield' and not source.phased_out and source.renowned)
        if fact in {
            FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_CARD_TYPES,
            FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_MANA_VALUES,
            FixedPublicStateFact.CONTROLLER_GRAVEYARD_HAS_INSTANT_AND_SORCERY,
        }:
            return self._graveyard_fact_quantity(source, fact)
        if fact in {
            FixedPublicStateFact.CONTROLLER_HAND_ADVANTAGE,
            FixedPublicStateFact.CONTROLLER_LIFE_ABOVE_STARTING,
            FixedPublicStateFact.CONTROLLER_AT_OR_BELOW_HALF_STARTING_LIFE,
        }:
            return self._player_fact_quantity(source, fact)
        return self._history_fact_quantity(source, condition)

    def _most_common_color_quantity(self, condition: FixedPublicStateConditionSpec) -> int | None:
        if self.public_object_resolver is None:
            return None
        counts = dict.fromkeys('WUBRG', 0)
        for candidate in self.state.cards.values():
            if candidate.zone != 'battlefield' or candidate.phased_out:
                continue
            row = self._public_object(candidate)
            assert row is not None
            for color in set(row.colors):
                if color not in counts:
                    raise SemanticNodeError('A current public permanent color is malformed')
                counts[color] += 1
        return int(counts[str(condition.fact_parameter)] >= max(counts.values()))

    def _graveyard_fact_quantity(
        self,
        source: Any,
        fact: FixedPublicStateFact,
    ) -> int | None:
        if self.public_object_resolver is None:
            return None
        rows = tuple(
            self._public_object(self.state.cards[object_id])
            for object_id in self.state.players[source.controller].zones["graveyard"]
        )
        if any(row is None for row in rows):
            return None
        public_rows = tuple(row for row in rows if row is not None)
        if fact is FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_CARD_TYPES:
            return len({value for row in public_rows for value in row.types})
        if fact is FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_MANA_VALUES:
            return len({row.mana_value for row in public_rows})
        present_types = {value for row in public_rows for value in row.types}
        return int({"instant", "sorcery"} <= present_types)

    def _history_fact_quantity(
        self,
        source: Any,
        condition: FixedPublicStateConditionSpec,
    ) -> int | None:
        fact = condition.fact
        assert isinstance(fact, FixedPublicStateFact)
        controller = source.controller
        history = getattr(self.state, "turn_history", None)
        turn_sequence = max(0, int(getattr(self.state, "turn_sequence", 0)))
        if history is None or history.schema_version != 1 or history.turn_sequence != turn_sequence:
            return None

        def events(kind: str) -> tuple[Any, ...]:
            return current_turn_history_events(
                history,
                turn_sequence=turn_sequence,
                kind=kind,
            )

        if fact is FixedPublicStateFact.ANY_CREATURE_DIED_THIS_TURN:
            return len(events("creature_died"))
        if fact is FixedPublicStateFact.CONTROLLER_ATTACKED_THIS_TURN:
            return sum(event.actor == controller for event in events("creature_attacked"))
        if fact is FixedPublicStateFact.CONTROLLER_PERMANENT_LEFT_THIS_TURN:
            if history.departure_history_version != 1:
                return None
            return sum(event.actor == controller for event in events("permanent_left"))
        if fact is FixedPublicStateFact.OPPONENT_LOST_LIFE_THIS_TURN:
            opponents = {seat for seat, player in self.state.players.items()
                         if seat != controller and player.in_game}
            return sum(event.amount for event in events("player_lost_life")
                       if event.target in opponents)

        if fact in {
            FixedPublicStateFact.CONTROLLER_LIFE_GAINED_THIS_TURN,
            FixedPublicStateFact.CONTROLLER_LIFE_LOST_THIS_TURN,
        }:
            event_kind = (
                "player_gained_life"
                if fact is FixedPublicStateFact.CONTROLLER_LIFE_GAINED_THIS_TURN
                else "player_lost_life"
            )
            return sum(
                event.amount
                for event in events(event_kind)
                if event.target == controller
            )
        event_kinds = {
            FixedPublicStateFact.CONTROLLER_PERMANENTS_SACRIFICED_THIS_TURN: (
                "permanent_sacrificed"
            ),
            FixedPublicStateFact.CONTROLLER_CREATURES_DIED_THIS_TURN: (
                "creature_died"
            ),
        }
        if fact in event_kinds:
            return sum(
                event.actor == controller for event in events(event_kinds[fact])
            )
        if fact in {
            FixedPublicStateFact.CONTROLLER_NONLAND_PERMANENTS_ENTERED_THIS_TURN,
            FixedPublicStateFact.CONTROLLER_LANDS_ENTERED_THIS_TURN,
            FixedPublicStateFact.CONTROLLER_OTHER_CREATURES_ENTERED_THIS_TURN,
            FixedPublicStateFact.CONTROLLER_ARTIFACTS_ENTERED_THIS_TURN,
        }:
            entered = tuple(
                event
                for event in events("permanent_entered")
                if event.actor == controller
            )
            if fact is (
                FixedPublicStateFact
                .CONTROLLER_NONLAND_PERMANENTS_ENTERED_THIS_TURN
            ):
                return sum("land" not in event.types for event in entered)
            if fact is FixedPublicStateFact.CONTROLLER_ARTIFACTS_ENTERED_THIS_TURN:
                return sum("artifact" in event.types for event in entered)
            if fact is FixedPublicStateFact.CONTROLLER_LANDS_ENTERED_THIS_TURN:
                return sum('land' in event.types for event in entered)
            return sum(
                "creature" in event.types
                and event.object_incarnation != source.logical_object_id
                for event in entered
            )
        attacks = events("creature_attacked")
        if fact in {
            FixedPublicStateFact.SOURCE_ATTACKED_THIS_TURN,
            FixedPublicStateFact.SOURCE_ATTACKED_BATTLE_THIS_TURN,
        }:
            return sum(
                event.object_incarnation == source.logical_object_id
                and (
                    fact is FixedPublicStateFact.SOURCE_ATTACKED_THIS_TURN
                    or event.target_kind == "battle"
                )
                for event in attacks
            )
        if fact is FixedPublicStateFact.CONTROLLER_ATTACKED_WITH_SUBTYPE_THIS_TURN:
            subtype = str(condition.fact_parameter or "")
            matching = tuple(
                event
                for event in attacks
                if event.actor == controller and subtype in event.types
            )
            if condition.schema_version == 3:
                return len(matching)
            return len(
                {
                    event.object_incarnation
                    for event in matching
                    if event.object_incarnation is not None
                }
            )
        return None

    def _player_fact_quantity(
        self,
        source: Any,
        fact: FixedPublicStateFact,
    ) -> int | None:
        controller = source.controller
        if fact is FixedPublicStateFact.CONTROLLER_HAND_ADVANTAGE:
            controller_count = len(self.state.players[controller].zones["hand"])
            opponent_counts = tuple(
                len(self.state.players[player].zones["hand"])
                for player in self.state.turn_order
                if player != controller and self.state.players[player].in_game
            )
            return int(
                bool(opponent_counts)
                and all(controller_count > count for count in opponent_counts)
            )
        starting_life = getattr(
            getattr(self.state, "config", None),
            "starting_life",
            None,
        )
        if type(starting_life) is not int or starting_life <= 0:
            return None
        life = int(self.state.players[controller].life)
        if fact is FixedPublicStateFact.CONTROLLER_LIFE_ABOVE_STARTING:
            return max(0, life - starting_life)
        if fact is (
            FixedPublicStateFact.CONTROLLER_AT_OR_BELOW_HALF_STARTING_LIFE
        ):
            return int(life * 2 <= starting_life)
        return None

    def snapshot(
        self,
        source: Any,
        condition: FixedPublicStateConditionSpec,
    ) -> FixedPublicStateConditionSnapshot:
        if condition.terms:
            snapshots=tuple(self.snapshot(source,leaf) for leaf in condition.terms)
            return replace(snapshots[0],term_snapshots=snapshots)
        controller = self.state.players[source.controller]
        kind = condition.kind
        turn_sequence = max(0, int(getattr(self.state, "turn_sequence", 0)))
        controller_draw_count = (
            drawn_this_turn(self, source.controller)
            if kind
            is FixedPublicStateConditionKind.CONTROLLER_DRAW_COUNT_AT_LEAST
            else 0
        )
        controller_spell_cast_count = 0
        controller_noncreature_spell_cast_count = 0
        controller_instant_sorcery_cast_count = 0
        if kind in {
            FixedPublicStateConditionKind.CONTROLLER_SPELL_CAST_COUNT_AT_LEAST,
            FixedPublicStateConditionKind
            .CONTROLLER_NONCREATURE_SPELL_CAST_COUNT_AT_LEAST,
            FixedPublicStateConditionKind
            .CONTROLLER_INSTANT_SORCERY_CAST_COUNT_AT_LEAST,
        }:
            cast_events = (
                event
                for event in current_turn_history_events(
                    getattr(self.state, "turn_history", None),
                    turn_sequence=turn_sequence,
                    kind="spell_cast",
                )
                if event.actor == source.controller
            )
            if kind is (
                FixedPublicStateConditionKind.CONTROLLER_SPELL_CAST_COUNT_AT_LEAST
            ):
                controller_spell_cast_count = sum(1 for _event in cast_events)
            elif kind is (
                FixedPublicStateConditionKind
                .CONTROLLER_NONCREATURE_SPELL_CAST_COUNT_AT_LEAST
            ):
                controller_noncreature_spell_cast_count = sum(
                    "creature" not in event.types for event in cast_events
                )
            else:
                controller_instant_sorcery_cast_count = sum(
                    bool({"instant", "sorcery"}.intersection(event.types))
                    for event in cast_events
                )
        opponent_poison_counter_counts = (
            tuple(
                max(0, int(getattr(self.state.players[other], "poison", 0)))
                for other in self.state.turn_order
                if other != source.controller
                and self.state.players[other].in_game
            )
            if kind
            is FixedPublicStateConditionKind.OPPONENT_POISON_COUNTER_AT_LEAST
            else ()
        )
        player_hand_counts = (
            tuple(
                len(self.state.players[player].zones["hand"])
                for player in self.state.turn_order
                if self.state.players[player].in_game
            )
            if kind
            is FixedPublicStateConditionKind.ANY_PLAYER_HAND_COUNT_AT_MOST
            else ()
        )
        controller_is_monarch = kind is (
            FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH
        ) and getattr(self.state, "monarch", None) == source.controller
        from ..city_blessing_model import player_has_citys_blessing
        controller_has_citys_blessing=(kind is FixedPublicStateConditionKind.CONTROLLER_HAS_CITYS_BLESSING
            and player_has_citys_blessing(controller))
        attached = (
            self.state.cards.get(source.attached_to)
            if kind is FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY
            and getattr(source, "attached_to", None)
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
            controller_draw_count=controller_draw_count,
            controller_spell_cast_count=controller_spell_cast_count,
            controller_noncreature_spell_cast_count=(
                controller_noncreature_spell_cast_count
            ),
            controller_instant_sorcery_cast_count=(
                controller_instant_sorcery_cast_count
            ),
            opponent_poison_counter_counts=opponent_poison_counter_counts,
            player_hand_counts=player_hand_counts,
            controller_is_monarch=controller_is_monarch,
            controller_has_citys_blessing=controller_has_citys_blessing,
            controller_speed=player_speed(controller) or 0,
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


def fixed_public_state_condition_matches(
    state: ContinuousRuntimeState,
    source: Any,
    condition: FixedPublicStateConditionSpec,
    *,
    public_object_resolver: Callable[[Any], ObjectQueryResult] | None,
    quantity_resolver: Callable[[Any, CharacteristicQuantitySpec], int] | None,
    require_available: bool = False,
) -> bool:
    """Evaluate one shared public-state condition at its canonical boundary."""

    resolver = _FixedPublicStateSnapshotResolver(
        state=state,
        public_object_resolver=public_object_resolver,
        quantity_resolver=quantity_resolver,
    )
    snapshot = resolver.snapshot(source, condition)
    if require_available and condition.kind in {
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST,
        FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST,
        FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
        FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS,
    } and snapshot.condition_quantity is None:
        raise FixedPublicStateConditionError("The public resolution condition is unavailable")
    return condition.matches(snapshot)


def _applicable_static_programs(
    semantics: "SemanticRegistry",
    source: Any,
    semantic_keys: Sequence[str],
) -> tuple["SemanticProgram", ...]:
    compatibility_programs = {
        program.key: program
        for program in semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(source),
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
        ),
        source_designations=permanent_numeric_designations(source),
    )


def _continuous_programs_for_source(
    semantics: "SemanticRegistry",
    source: Any,
    static_component_resolver: Callable[[Any], Sequence[str]] | None,
    early_static_component_resolver: Callable[[Any, Layer], Sequence[str]] | None = None,
    maximum_layer: Layer | None = None,
    ability_source_participation: Callable[[Any,str],bool|None] | None = None,
) -> tuple["SemanticProgram", ...]:
    if source.zone == 'graveyard':
        from ..continuous_conditions import GRAVEYARD_QUERY_GRANT_HANDLER_ID, GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID
        return tuple(p for p in semantics.runtime_handler_programs_for_oracle(source.oracle_id,
            active_zone='graveyard', event='characteristics.evaluate') if any(
            d.get('handler_id') in {'continuous.characteristics.graveyard-public-state.v1',
                GRAVEYARD_QUERY_GRANT_HANDLER_ID,GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID} for d in p.handlers))
    if static_component_resolver is not None:
        programs = {program.key: program for program in _applicable_static_programs(
            semantics, source, static_component_resolver(source))}
        registry = default_continuous_effect_component_registry()
        for program in _continuous_programs_for_source(semantics, source, None):
            layer = min((registry.source_component_layer(descriptor) for descriptor in program.handlers),
                default=Layer.ABILITY)
            if layer >= Layer.ABILITY:
                if ability_source_participation is not None and (maximum_layer is None or maximum_layer>=Layer.ABILITY):
                    present=ability_source_participation(source,program.key)
                    if present is not None:
                        programs.pop(program.key,None)
                        if present:programs[program.key]=program
                continue
            if maximum_layer is not None and maximum_layer <= layer:
                programs.pop(program.key, None)
                continue
            if early_static_component_resolver is None:
                raise SemanticNodeError('Early-layer static components require their source-layer query')
            programs.pop(program.key, None)
            if program.key in early_static_component_resolver(source, layer):
                programs[program.key] = program
        return tuple(programs[key] for key in sorted(programs))
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
                copiable_oracle_id(source),
                active_zone="battlefield",
                event="characteristics.evaluate",
            )
        )
        if not program.provenance.get('granted_only')
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


def _component_public_state_snapshot(
    source, descriptor, *, maximum_layer, public_state_resolver, setting_state_resolver,
):
    """Keep early-layer settings separate from characteristic query conditions."""
    handler_id = descriptor.get('handler_id')
    from ..semantic_runtime.quantified_anthems import QUANTIFIED_ANTHEM_HANDLER
    from ..semantic_runtime.shared_type_anthems import SHARED_TYPE_ANTHEM_HANDLER
    if handler_id in {QUANTIFIED_ANTHEM_HANDLER,SHARED_TYPE_ANTHEM_HANDLER} and maximum_layer is not None and maximum_layer<Layer.POWER_TOUGHNESS:
        return True,None
    if handler_id == 'continuous.characteristics.conditional-creature-type-removal.v1':
        from ..semantic_runtime.fixed_characteristic_settings import ConditionalCreatureTypeRemovalHandler
        ConditionalCreatureTypeRemovalHandler().validate(descriptor)
        # Devotion reads through layer 3 (CR 700.5a); this component emits
        # only layer 4, so its live condition cannot participate in that read.
        if maximum_layer is not None and maximum_layer < Layer.TYPE:
            return True, None
        return False, setting_state_resolver.snapshot(source,
            FixedPublicStateConditionSpec.from_dict(descriptor['source_condition']))
    is_setting = handler_id in ('continuous.characteristics.fixed-public-setting.v2',
        'continuous.ability.public-state-color-grant.v1')
    if is_setting:
        raw_condition = descriptor.get('source_condition')
        return (False, None if raw_condition is None else setting_state_resolver.snapshot(
            source, FixedPublicStateConditionSpec.from_dict(raw_condition)))
    is_conditional = handler_id in (
        FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
        FIXED_PUBLIC_STATE_GRANTED_ABILITY_HANDLER_ID,
        CONDITIONAL_DEFENDER_PERMISSION_HANDLER, CONDITIONAL_AS_UNBLOCKED_HANDLER,
        'continuous.block-capacity.public-state.v1', 'continuous.declaration.public-state.v1',
        'continuous.characteristics.graveyard-public-state.v1',
        'continuous.ability.graveyard-public-state-grant.v2',
        'continuous.characteristics.public-state-keywords.v1',
        'continuous.characteristics.conditional-chosen-designation.v1',
        'continuous.ability.conditional-flash.v1',
    )
    if is_conditional and maximum_layer is not None and maximum_layer < Layer.ABILITY:
        # These components emit only layers 6/7; their query conditions must
        # not recurse through the layer-5 characteristic boundary.
        return True, None
    return False, (public_state_resolver.snapshot(source,
        FixedPublicStateConditionSpec.from_dict(descriptor['source_condition'])) if is_conditional else None)


def _descriptor_characteristic_quantity(source,descriptor,*,quantity_resolver,maximum_layer):
    """Project supported continuous quantities once through the layer-safe owner."""
    from ..semantic_runtime.quantified_anthems import QUANTIFIED_ANTHEM_HANDLER
    modifier=descriptor.get('modifier')
    raw=modifier.get('quantity') if isinstance(modifier,Mapping) else None
    if (descriptor.get('handler_id') in {ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID,QUANTIFIED_ANTHEM_HANDLER}
            and isinstance(raw,Mapping) and quantity_resolver is not None
            and (maximum_layer is None or maximum_layer>=Layer.POWER_TOUGHNESS)):
        return quantity_resolver(source,CharacteristicQuantitySpec.from_dict(raw))
    return None


def _descriptor_public_type_objects(source,descriptor,*,state,resolver):
    from ..semantic_runtime.shared_type_anthems import SHARED_TYPE_ANTHEM_HANDLER
    if descriptor.get('handler_id')!=SHARED_TYPE_ANTHEM_HANDLER:
        return None
    if descriptor.get('relation')=='source_attached_to_object' and not getattr(source,'attached_to',None):
        return None
    if resolver.public_object_resolver is None:
        raise SemanticNodeError('Shared-type effects require a cycle-safe public object resolver')
    objects=(card for card in state.cards.values() if card.zone=='battlefield' and not card.phased_out)
    return tuple(resolver._public_object(card) for card in sorted(objects,key=lambda card:card.object_id))


def _continuous_program_instances(semantics, source, programs, granted_resolver):
    if granted_resolver is None:
        return tuple((program, None, None) for program in programs)
    from ..rules.granted_characteristic_shapes import granted_characteristic_handlers_are_closed
    result = [(program, None, None) for program in programs
        if not (program.provenance.get('granted_only') and program.ability_id.startswith('static:granted:')
            and program.event == 'characteristics.evaluate')]
    for ordinal, (key, timestamp) in enumerate(granted_resolver(source)):
        program = semantics.get(key)
        if (program is None or program.provenance.get('granted_only') is not True
                or program.active_zone != 'battlefield' or program.event != 'characteristics.evaluate'
                or not program.ability_id.startswith('static:granted:') or program.effects
                or not granted_characteristic_handlers_are_closed(program.handlers)):
            raise SemanticNodeError('Current granted characteristic references an unknown or different static child')
        result.append((program, timestamp, ordinal))
    return tuple(result)


def _granted_characteristic_timestamp_order(effects, grant_order):
    groups = {}
    for effect in effects:
        groups.setdefault((effect.source_id, effect.layer, effect.sublayer, effect.timestamp), []).append(effect)
    replacements = {}
    for group in groups.values():
        if not any(effect.effect_id in grant_order for effect in group):
            continue
        ordered = sorted(group, key=lambda effect: (effect.effect_id in grant_order,
            grant_order.get(effect.effect_id, (0, 0, '')), effect.effect_id))
        for previous, effect in zip(ordered, ordered[1:]):
            if effect.effect_id in grant_order:
                replacements[effect.effect_id] = replace(effect,
                    depends_on=tuple(sorted({*effect.depends_on, previous.effect_id})))
    return tuple(replacements.get(effect.effect_id, effect) for effect in effects)


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
    setting_quantity_resolver: Callable[[Any, CharacteristicQuantitySpec], int] | None = None,
    static_component_resolver: Callable[[Any], Sequence[str]] | None = None,
    early_static_component_resolver: Callable[[Any, Layer], Sequence[str]] | None = None,
    static_component_presence_only: bool = False,
    granted_characteristic_resolver: Callable[[Any], Sequence[tuple[str, int]]] | None = None,
    ability_source_participation: Callable[[Any,str],bool|None] | None = None,
    generated_effect_sources: dict[str,tuple[str,str]] | None = None,
) -> tuple[ContinuousEffect, ...]:
    registry = default_continuous_effect_component_registry()
    registered_handler_ids = frozenset(
        str(row["handler_id"]) for row in registry.inventory()
    )
    if metrics is not None:
        metrics.collection_calls += 1
    effects: list[ContinuousEffect] = []
    grant_order = {}
    public_state_resolver = _FixedPublicStateSnapshotResolver(
        state=state,
        public_object_resolver=public_object_resolver,
        quantity_resolver=quantity_resolver,
    )
    setting_state_resolver = _FixedPublicStateSnapshotResolver(
        state=state, public_object_resolver=None,
        quantity_resolver=setting_quantity_resolver or quantity_resolver,
    )

    for seat in state.turn_order:
        player = state.players[seat]
        for object_id in [*player.zones['battlefield'], *player.zones['graveyard']]:
            source = state.cards[object_id]
            if metrics is not None and source.zone == 'battlefield':
                metrics.battlefield_objects_inspected += 1
            if (
                (source.owner if source.zone == 'graveyard' else source.controller) != seat
                or source.phased_out
                or getattr(source, "face_down", False) and granted_characteristic_resolver is None
            ):
                continue
            if source.zone == 'graveyard':
                from ..continuous_conditions import OwnerRelativeConditionSource
                source = OwnerRelativeConditionSource(source)
            programs = () if getattr(source, 'face_down', False) else _continuous_programs_for_source(
                semantics, source, static_component_resolver, early_static_component_resolver, maximum_layer,
                ability_source_participation,
            )
            if metrics is not None:
                metrics.card_program_lookups += 1
            for program, grant_timestamp, occurrence in _continuous_program_instances(
                    semantics, source, programs, granted_characteristic_resolver):
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
                    if static_component_presence_only and not registry.may_affect_static_components(descriptor):
                        continue
                    skip, public_state = _component_public_state_snapshot(source, descriptor,
                        maximum_layer=maximum_layer, public_state_resolver=public_state_resolver,
                        setting_state_resolver=setting_state_resolver)
                    if skip:
                        continue
                    resolved_quantity = _descriptor_characteristic_quantity(source,descriptor,
                        quantity_resolver=quantity_resolver,maximum_layer=maximum_layer)
                    from ..soulbond_model import SOULBOND_GRANT_HANDLER
                    paired_object = None
                    if descriptor.get('handler_id') == SOULBOND_GRANT_HANDLER:
                        from ..soulbond_state import paired_object_identity
                        paired_object = paired_object_identity(state, source)
                    context = ContinuousEffectSourceContext(
                        source_object_id=source.object_id,
                        source_ref=source.ref,
                        source_controller=source.controller,
                        source_timestamp=max(
                            0, int(source.zone_timestamp), grant_timestamp if grant_timestamp is not None else 0
                        ),
                        component_id=(
                            f"{program.key}:{descriptor_index}" + (f':grant:{occurrence}' if occurrence is not None else '')
                        ),
                        source_logical_object_id=source.logical_object_id,
                        source_counters=FrozenMap(
                            getattr(source, "counters", {}) or {}
                        ),
                        source_designations=FrozenMap({
                            key: value
                            for key, value in (getattr(source, "annotations", {}) or {}).items()
                            if key in {"chosen_color", "chosen_creature_type"}
                        }),
                        public_state=public_state,
                        resolved_quantity=resolved_quantity,
                        paired_object=paired_object,
                        public_type_objects=_descriptor_public_type_objects(source,descriptor,state=state,
                                                                           resolver=public_state_resolver),
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
                    if generated_effect_sources is not None:
                        generated_effect_sources.update({effect.effect_id:(source.object_id,program.key) for effect in lowered})
                    if grant_timestamp is not None:
                        grant_order.update({effect.effect_id:(grant_timestamp, occurrence, program.key) for effect in lowered})
                    if metrics is not None:
                        metrics.effects_produced += len(lowered)
    return tuple(
        sorted(
            _granted_characteristic_timestamp_order(effects, grant_order),
            key=lambda effect: (
                int(effect.layer),
                effect.sublayer,
                effect.timestamp,
                effect.effect_id,
            ),
        )
    )
