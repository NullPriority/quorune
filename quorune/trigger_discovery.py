from __future__ import annotations
from .event_counter_reference import event_counter_recipient_context
from .rules.block_trigger_multiplicity import block_trigger_already_collected
from .entry_copy import copiable_oracle_id
from .characteristic_read_frames import characteristic_read_frame
from .evoke import is_evoked_annotation
from .keyword_abilities import normalized_characteristic_keywords

import copy
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
    canonical_ability_fragments,
    granted_triggered_specs,
)
from .semantic_runtime.current_ability_components import (
    program_has_current_ability_fragments,
)
from .creature_power_damage import (
    capture_creature_power_damage_source_lki,
    CREATURE_POWER_DAMAGE_LKI_CONTEXT,
    occurrence_power_trigger_context,
)
from .errors import GameRuleError
from .granted_activation_model import GRANTING_ATTACHMENT_PAYMENT_FIELD, GrantingAttachmentPayment
from .increment_model import CONDITION_FIELD as INCREMENT_CONDITION_FIELD
from .rules.increment_condition import current_increment_condition
from .continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionSpec,
)
from .rules.event_subscriptions import (
    DRAW_EXCEPT_FIRST_OWN_STEP_EVENT, LAND_PLAY_EVENT, PLAYED_LAND_REFERENCE_FIELD, FixedEventSubscriptionSet,
    intervening_recheck_condition, public_state_intervening_actual,
    source_event_intervening_recheck,
)
from .evolve import EVOLVE_EVENT_CONDITION_FIELD
from .rules.evolve_entrant import evolve_entrant_trigger_context, evolve_event_condition_holds
from .rules.target_announcements import TARGET_ANNOUNCEMENT_EVENT
from .rules.trigger_source_information import trigger_attachment_context
from .echo import (
    ECHO_CONTROL_CONDITION_FIELD,
    EchoError,
    echo_control_condition_holds,
)
from .death_return import (
    DEATH_RETURN_EVENT_CONDITION_FIELD,
    DeathReturnError,
    death_return_condition_holds,
    death_return_counter_snapshot,
)
from .model import CardInstance, StackItem
from .activation_usage import commit_trigger_usage
from .renown import (
    RENOWN_EVENT_CONDITION_FIELD,
    RenownError,
    renown_condition_holds,
)
from .modular import (
    MODULAR_COUNTER_COUNT_FIELD,
    MODULAR_COUNTER_SNAPSHOT_FIELD,
    ModularError,
    modular_counter_count,
    modular_counter_snapshot,
)
from .semantics import SemanticProgram
from .spell_history_transform import (
    SPELL_HISTORY_TRANSFORM_CONDITION_FIELD,
    SPELL_HISTORY_TRANSFORM_COVERAGE,
    SpellHistoryTransformError,
    spell_history_transform_condition_holds,
)
from .semantic_runtime.ability_fragments import fragments_from_descriptors
from .trigger_processing import enqueue_trigger_batch, trigger_occurrence_observations
from .trigger_participation import (
    StaticTriggerParticipation,
    TriggerMultiplierPredicate,
    TriggerMultiplierSpec,
)
from .util import stable_json


from .discard_effect_origin import OPPONENT_SOURCE_DISCARD_EVENT, event_source_zone

_DEPARTURE_EVENTS = frozenset(
    {
        OPPONENT_SOURCE_DISCARD_EVENT,
        "artifact.graveyard",
        "creature.dies",
        "permanent.graveyard",
        "permanent.leave",
        "spell.countered",
    }
)
_ENTER_EVENTS = frozenset(
    {
        "permanent.enter",
        "artifact.enter",
        "creature.enter",
        "land.enter",
        "enchantment.enter",
    }
)

class TriggerDiscoveryHost(Protocol):
    state: Any
    active_seats: Sequence[str]
    seats: Sequence[str]
    semantics: Any

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[CardInstance]: ...

    def _effective_card_data(
        self, card: str | CardInstance
    ) -> Mapping[str, Any]: ...

    def _copyable_characteristics(self, card: CardInstance) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _resolve_object(
        self, actor: str, ref: str, *, zones: set[str]
    ) -> CardInstance: ...

    def _numeric_stat(self, object_id: str, stat: str) -> int: ...

    def _fixed_public_state_condition_holds(
        self,
        source: CardInstance,
        condition: FixedPublicStateConditionSpec,
    ) -> bool: ...

    def card_record(self, card: CardInstance) -> Any: ...

    def semantic_program_is_current_trusted(
        self, program: SemanticProgram
    ) -> bool: ...

    def _pause_for_unsupported_semantic(
        self,
        *,
        program: SemanticProgram,
        event: str,
        source: CardInstance,
    ) -> None: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...


def semantic_event_value(
    host: TriggerDiscoveryHost,
    value: Any,
    *,
    source: CardInstance,
    context: Mapping[str, Any],
) -> Any:
    if value=='$source.logical_object_id':
        identity=(context.get('source_logical_object_id') if 'intervening_condition_controller' in context
            else getattr(source,'logical_object_id',None))
        if type(identity)is not str or not identity:
            raise GameRuleError('Event source requires its nonempty logical object identity')
        return identity
    locked_controller = context.get(
        "intervening_condition_controller",
        source.controller,
    )
    substitutions = {
        "$source.controller": locked_controller,
        "$source.owner": source.owner,
        "$source.ref": source.ref,
        "$source.object_id": source.object_id,
        "$active_player": host.state.active_player,
    }
    if isinstance(value, str) and value in substitutions:
        return substitutions[value]
    if isinstance(value, str) and value.startswith("$context."):
        return context.get(value.removeprefix("$context."))
    if isinstance(value, list):
        return [
            semantic_event_value(
                host,
                item,
                source=source,
                context=context,
            )
            for item in value
        ]
    return value


def _keyword_intervening_condition_actual(
    host: TriggerDiscoveryHost,
    field: str,
    *,
    source: CardInstance,
    context: Mapping[str, Any],
) -> bool:
    """Evaluate typed keyword conditions outside the generic dispatcher."""
    if field==INCREMENT_CONDITION_FIELD:
        try:return current_increment_condition(host,source,context)
        except ValueError as exc:raise GameRuleError(str(exc)) from exc
    from .soulbond_model import SOULBOND_SELF_CONDITION, SOULBOND_OTHER_CONDITION
    if field in {SOULBOND_SELF_CONDITION, SOULBOND_OTHER_CONDITION}:
        from .soulbond_state import intervening_pair_available
        return intervening_pair_available(host, source, context, other_entry=field == SOULBOND_OTHER_CONDITION)

    if field == RENOWN_EVENT_CONDITION_FIELD:
        try:
            return renown_condition_holds(source, context)
        except RenownError as exc:
            raise GameRuleError(str(exc)) from exc
    if field != EVOLVE_EVENT_CONDITION_FIELD:
        raise GameRuleError(
            f"Unsupported typed keyword condition field {field!r}"
        )
    return evolve_event_condition_holds(host, source, context)


def _extended_action_condition_actual(host,condition,*,source,context):
    field=condition.get('field')
    from .attack_batch_model import ATTACK_BATCH_CONDITION, DECLARED_ATTACK_POWER_FIELD
    from .land_entry_condition_model import (
        ENTRANT_LAND_ADVANTAGE_FIELD, OTHER_LAND_ARRIVAL_FIELD,
        entrant_land_advantage_leaf_is_closed, other_land_arrival_leaf_is_closed,
    )
    from .block_characteristic_model import BLOCK_CHARACTERISTIC_CONDITION
    from .qualified_attack_model import ATTACK_FACT_EQUAL_POWER_TOUGHNESS, declared_attacker_has_equal_power_and_toughness
    from .rules.attacked_player_life import ATTACKED_PLAYER_LIFE_FIELD, attacked_player_life_holds
    from .rules.entrant_name_absence import ENTRANT_NAME_ABSENCE_FIELD, entrant_name_absence_actual
    from .spell_history_ordinal_model import QUALIFIED_SPELL_ORDINAL_FIELD
    from .entry_designations import LINKED_OPPONENT_UPKEEP_FIELD, LINKED_PLAYER_CAST_FIELD
    from .rules.cast_target_copy import CAST_TARGET_COPY_CONDITION_FIELD, cast_target_copy_condition_holds
    from .rules.cast_source_spell_copy import SOURCE_CAST_COPY_CONDITION_FIELD, source_cast_copy_condition_holds
    from .rules.cast_creature_set_spell_copy import (
        CAST_CREATURE_SET_COPY_CONDITION, cast_creature_set_copy_condition_value,
    )
    try:
        if field == ENTRANT_LAND_ADVANTAGE_FIELD:
            from .rules.event_subscriptions import entrant_land_advantage_holds

            if not entrant_land_advantage_leaf_is_closed(condition):
                raise ValueError("Invalid entering-opponent land advantage condition")
            return True, entrant_land_advantage_holds(host, context, trigger_controller=source.controller)
        if field == OTHER_LAND_ARRIVAL_FIELD:
            from .rules.event_subscriptions import other_land_arrival_holds

            if not other_land_arrival_leaf_is_closed(condition):
                raise ValueError("Invalid opponent land history condition")
            return True, other_land_arrival_holds(host.state, context)
        if field == ENTRANT_NAME_ABSENCE_FIELD:
            return True, entrant_name_absence_actual(host, condition, context, source.controller)
        if field == CAST_CREATURE_SET_COPY_CONDITION:
            return True, cast_creature_set_copy_condition_value(host, condition, context, source)
        if field == CAST_TARGET_COPY_CONDITION_FIELD:
            return True, cast_target_copy_condition_holds(host, condition, context, source)
        if field == SOURCE_CAST_COPY_CONDITION_FIELD:
            return True, source_cast_copy_condition_holds(host, condition, context, source)
        if field in {LINKED_OPPONENT_UPKEEP_FIELD, LINKED_PLAYER_CAST_FIELD}:
            from .rules.linked_entry_opponent import current_linked_entry_opponent, current_linked_entry_player

            if set(condition) != {"field", "op", "value"} or condition["op"] != "truthy" or condition["value"] is not True:
                raise ValueError("A linked entry seat condition requires its closed event comparison")
            resolve_seat = current_linked_entry_player if field == LINKED_PLAYER_CAST_FIELD else current_linked_entry_opponent
            seat = resolve_seat(host, source)
            return True, seat is not None and context.get("player") == seat
        from .rules.spell_cast_events import SOURCE_POWER_CAST_FIELD, opposing_noncreature_cast_below_source_power
        if field == SOURCE_POWER_CAST_FIELD:
            return True, opposing_noncreature_cast_below_source_power(host, condition, context, source)
        if field == 'cast_mana_value_matches_source_counter':
            from .rules.source_counter_cast_value import SourceCounterCastValueSpec
            return True,SourceCounterCastValueSpec.from_condition(condition).matches(source,context)
        if field == ATTACKED_PLAYER_LIFE_FIELD:
            return True, attacked_player_life_holds(host, context, source_object_id=source.object_id, controller=source.controller)
        if field==BLOCK_CHARACTERISTIC_CONDITION:
            from .rules.block_characteristics import block_counterpart_matches
            return True,block_counterpart_matches(condition,context)
        if field==QUALIFIED_SPELL_ORDINAL_FIELD:
            from .rules.spell_history_ordinals import qualified_spell_cast_number
            return True,qualified_spell_cast_number(host.state,condition,context)
        if field == ATTACK_FACT_EQUAL_POWER_TOUGHNESS:
            return True, declared_attacker_has_equal_power_and_toughness(context)
        if field==ATTACK_BATCH_CONDITION:
            from .rules.attack_batches import qualified_attack_match_count
            return True,qualified_attack_match_count(source,condition,context)
        if field == DECLARED_ATTACK_POWER_FIELD:
            from .rules.attack_batches import declared_attack_power_total
            return True, declared_attack_power_total(
                context, source_object_id=source.object_id,
                source_logical_object_id=source.logical_object_id, controller=source.controller,
            )
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    return False,None


def _source_attachment_condition_actual(
    host: TriggerDiscoveryHost,
    field: str,
    condition: Mapping[str, Any],
    source: CardInstance,
) -> str | None:
    if field == "source_attachment_target_ref":
        attached = host.state.cards.get(source.attached_to or "")
        return attached.ref if attached is not None else None
    if field == "source_attached_player":
        from .attachments import current_attached_player_seat

        return current_attached_player_seat(source, host.state.players)
    from .scheduled_player import current_attachment_controller

    return current_attachment_controller(host, source, condition)


def _semantic_condition_actual(
    host: TriggerDiscoveryHost,
    condition: Mapping[str, Any],
    *,
    source: CardInstance,
    context: Mapping[str, Any],
) -> Any:
    field = str(condition.get("field") or "")
    if not field:
        raise GameRuleError("Semantic event condition requires a field")
    if field=='ravenous_entry_x_at_least':
        from .rules.ravenous import ravenous_condition_value
        try:return ravenous_condition_value(condition,source,context)
        except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    if field in {'unpaid_creature_entry_group_has_first_member', 'source_entry_cast_by_controller'}:
        from .rules.entry_cast_information import entry_condition_actual

        return entry_condition_actual(host, field, condition, source=source, context=context)
    handled,actual=_extended_action_condition_actual(host,condition,source=source,context=context)
    if handled:return actual
    if field == "counter_names":
        names = context.get(field)
        if (not isinstance(names, Sequence) or isinstance(names, (str, bytes))
                or any(type(name) is not str or not name for name in names)
                or list(names) != sorted(set(names))):
            raise GameRuleError("Counter-qualified zone event requires sealed counter facts")
        return names
    from .rules.trigger_source_information import damage_history_condition_value
    if (damage_history := damage_history_condition_value(host, field, source=source, context=context)) is not None:
        return damage_history
    if field == FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD:
        return public_state_intervening_actual(
            host,
            condition,
            source=source,
            context=context,
        )
    if field == "source_controller_subtype_count":
        subtype = str(condition.get("subtype") or "").casefold()
        if not subtype:
            raise GameRuleError("Subtype-count condition requires a subtype")
        return sum(
            1
            for object_id in host.state.players[source.controller].zones[
                "battlefield"
            ]
            if host.state.cards[object_id].controller == source.controller
            and subtype
            in host._type_parts(
                str(host._effective_card_data(object_id).get("type_line") or "")
            )[1]
        )
    if field == "source_controller_type_count":
        card_type = str(condition.get("type") or "").casefold()
        if not card_type:
            raise GameRuleError("Type-count condition requires a card type")
        return sum(
            1
            for object_id in host.state.players[source.controller].zones[
                "battlefield"
            ]
            if host.state.cards[object_id].controller == source.controller
            and card_type
            in host._type_parts(
                str(host._effective_card_data(object_id).get("type_line") or "")
            )[0]
        )
    if field == "source_controller_controls_highest_artifact_mana_value":
        controlled_values: list[float] = []
        all_values: list[float] = []
        for active_seat in host.active_seats:
            for object_id in host.state.players[active_seat].zones["battlefield"]:
                permanent = host.state.cards[object_id]
                if permanent.phased_out:
                    continue
                data = host._effective_card_data(permanent)
                types, _, _ = host._type_parts(str(data.get("type_line") or ""))
                if "artifact" not in types:
                    continue
                value = float(data.get("mana_value") or 0)
                all_values.append(value)
                if permanent.controller == source.controller:
                    controlled_values.append(value)
        return bool(
            controlled_values and max(controlled_values) == max(all_values)
        )
    if field in {EVOLVE_EVENT_CONDITION_FIELD, RENOWN_EVENT_CONDITION_FIELD,INCREMENT_CONDITION_FIELD,
                 'soulbond_self_entry_available', 'soulbond_other_entry_available'}:
        return _keyword_intervening_condition_actual(
            host,
            field,
            source=source,
            context=context,
        )
    if field == ECHO_CONTROL_CONDITION_FIELD:
        try:
            return echo_control_condition_holds(source, context)
        except EchoError as exc:
            raise GameRuleError(str(exc)) from exc
    if field == DEATH_RETURN_EVENT_CONDITION_FIELD:
        counter = condition.get("counter")
        counters = context.get("death_return_counter_snapshot")
        if counters is None:
            counters = source.counters
        if not isinstance(counters, Mapping):
            raise GameRuleError(
                "Death-return event requires last-known counter facts"
            )
        try:
            return death_return_condition_holds(counters, str(counter or ""))
        except DeathReturnError as exc:
            raise GameRuleError(str(exc)) from exc
    if field == SPELL_HISTORY_TRANSFORM_CONDITION_FIELD:
        try:
            return spell_history_transform_condition_holds(
                host.state.turn_history,
                current_turn_sequence=host.state.turn_sequence,
                source=source,
                context=context,
                mode=condition.get("mode"),
            )
        except SpellHistoryTransformError as exc:
            raise GameRuleError(str(exc)) from exc
    if field.startswith("source_annotation."):
        from .rules.chosen_type_events import CHOSEN_TYPE_FIELD,chosen_type_event_value
        if field==CHOSEN_TYPE_FIELD:
            try:return chosen_type_event_value(source)
            except (TypeError,ValueError) as exc:raise GameRuleError(str(exc)) from exc
        return source.annotations.get(field.removeprefix("source_annotation."))
    if field == "source_active_face":
        if source.active_face:
            return source.active_face
        record = host.card_record(source)
        if record is not None and record.faces:
            return str(record.faces[0].get("name") or "")
        return host._effective_card_data(source).get("name")
    if field in ("source_attachment_target_ref", "source_attached_player", "source_attachment_controller"):
        return _source_attachment_condition_actual(host, field, condition, source)
    if field == "source_controller_is_active_player":
        return source.controller == host.state.active_player
    return context.get(field)


def _condition_mentions_field(
    condition: Mapping[str, Any] | None,
    field: str,
) -> bool:
    if not isinstance(condition, Mapping):
        return False
    if condition.get("field") == field:
        return True
    for key in ("all", "any"):
        values = condition.get(key)
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            if any(
                _condition_mentions_field(value, field)
                for value in values
                if isinstance(value, Mapping)
            ):
                return True
    nested = condition.get("not")
    return isinstance(nested, Mapping) and _condition_mentions_field(
        nested, field
    )


def _echo_control_context(
    source: Any,
    event_condition: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not _condition_mentions_field(
        event_condition,
        ECHO_CONTROL_CONDITION_FIELD,
    ):
        return {}
    return {
        "echo_control_acquisition_controller": source.controller,
        "echo_control_acquisition_timestamp": (
            source.acquired_control_timestamp
        ),
    }


def _semantic_condition_operator_matches(
    op: str,
    actual: Any,
    expected: Any,
) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in (expected or [])
    if op == "not_in":
        return actual not in (expected or [])
    if op == "contains_any":
        return bool(set(actual or []).intersection(expected or []))
    if op == "count_gte":
        if (
            not isinstance(actual, Sequence)
            or isinstance(actual, (str, bytes))
            or type(expected) is not int
            or expected < 0
        ):
            raise GameRuleError(
                "Semantic event count_gte requires a sequence and "
                "nonnegative integer"
            )
        return len(actual) >= expected
    if op == "gte":
        return actual is not None and actual >= expected
    if op == "gt":
        return actual is not None and actual > expected
    if op == "lte":
        return actual is not None and actual <= expected
    if op == "lt":
        return actual is not None and actual < expected
    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not bool(actual)
    raise GameRuleError(
        f"Unsupported semantic event condition operator {op!r}"
    )


def semantic_event_condition_matches(
    host: TriggerDiscoveryHost,
    condition: Mapping[str, Any],
    *,
    source: CardInstance,
    context: Mapping[str, Any],
) -> bool:
    """Evaluate one read-only declarative condition over normalized facts."""

    if "all" in condition:
        values = condition.get("all")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise GameRuleError("Semantic event 'all' must be a list")
        return all(
            semantic_event_condition_matches(
                host,
                dict(item),
                source=source,
                context=context,
            )
            for item in values
            if isinstance(item, Mapping)
        )
    if "any" in condition:
        values = condition.get("any")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise GameRuleError("Semantic event 'any' must be a list")
        return any(
            semantic_event_condition_matches(
                host,
                dict(item),
                source=source,
                context=context,
            )
            for item in values
            if isinstance(item, Mapping)
        )
    if "not" in condition:
        nested = condition.get("not")
        if not isinstance(nested, Mapping):
            raise GameRuleError("Semantic event 'not' must be an object")
        return not semantic_event_condition_matches(
            host,
            nested,
            source=source,
            context=context,
        )

    actual = _semantic_condition_actual(
        host,
        condition,
        source=source,
        context=context,
    )
    expected = semantic_event_value(
        host,
        condition.get("value"),
        source=source,
        context=context,
    )
    return _semantic_condition_operator_matches(
        str(condition.get("op") or "eq"),
        actual,
        expected,
    )


def semantic_intervening_condition_matches(
    host: TriggerDiscoveryHost,
    program: SemanticProgram,
    *,
    source: CardInstance | None,
    context: Mapping[str, Any],
    controller: str | None = None,source_object_id: str | None = None,
) -> bool:
    """Recheck one intervening-if program at its trigger-owner boundary."""

    source_event_result = source_event_intervening_recheck(
        host, program, context=context, controller=controller, source_object_id=source_object_id,
    )
    if source_event_result is not None:
        return source_event_result
    return bool(
        source is not None
        and program.event_condition is not None
        and (
            FIXED_PUBLIC_STATE_INTERVENING_COVERAGE in program.coverage
            or source.zone == program.active_zone
        )
        and semantic_event_condition_matches(
            host,
            intervening_recheck_condition(program, context=context, controller=controller),
            source=source,
            context=context,
        )
    )


def _selected_event_subscription(
    program: SemanticProgram,
    event: str,
) -> tuple[bool, bool, Mapping[str, Any] | None]:
    """Return match, self-event, and condition for one occurrence."""

    subscriptions = FixedEventSubscriptionSet.from_condition(
        program.event_condition
    )
    if subscriptions is None:
        self_event = program.event.endswith(".self")
        program_event = (
            program.event.removesuffix(".self")
            if self_event
            else program.event
        )
        return program_event == event, self_event, program.event_condition
    for subscription in subscriptions.subscriptions:
        self_event = subscription.event.endswith(".self")
        subscribed_event = (
            subscription.event.removesuffix(".self")
            if self_event
            else subscription.event
        )
        if subscribed_event == event:
            return (
                True,
                self_event,
                dict(subscription.condition)
                if subscription.condition is not None
                else None,
            )
    return False, False, None


def semantic_event_matches(
    host: TriggerDiscoveryHost,
    program: SemanticProgram,
    source: CardInstance,
    event: str,
    context: Mapping[str, Any],
    *,
    source_zone: str | None = None,
) -> bool:
    event_matches, self_event, event_condition = _selected_event_subscription(
        program,
        event,
    )
    if not event_matches or program.active_zone != event_source_zone(event, source_zone or source.zone):
        return False
    from .rules.event_subscriptions import GRAVEYARD_DEPARTURE_LOOKBACK_EVENT
    if event == GRAVEYARD_DEPARTURE_LOOKBACK_EVENT and context.get("to") == "stack":
        from .rules.grouped_graveyard_departure import COVERAGE
        if COVERAGE not in program.coverage:
            return False
    if self_event and str(context.get("card") or "") != source.ref:
        return False
    trigger_controller = (
        str(context.get("previous_controller"))
        if (
            self_event
            and context.get("previous_controller") is not None
            and event in _DEPARTURE_EVENTS
        )
        else source.controller
    )
    if trigger_controller not in host.active_seats:
        return False
    if event_condition is not None:
        return semantic_event_condition_matches(
            host,
            event_condition,
            source=source,
            context=context,
        )
    if event == "land.enter":
        entered = host._resolve_object(
            source.controller,
            str(context.get("card")),
            zones={"battlefield"},
        )
        return entered.controller == source.controller
    if event == "card.second_draw":
        return context.get("player") == source.controller
    if event == "step.begin":
        return (
            context.get("player") == source.controller
            and context.get("step") == "beginning_combat"
        )
    if event == "artifact.enter":
        entered = host._resolve_object(
            source.controller,
            str(context.get("card")),
            zones={"battlefield"},
        )
        return entered.controller == source.controller
    if event == "creature.dies" and not self_event:
        return context.get("previous_controller") == source.controller
    return True


def _trigger_controller(
    program: SemanticProgram,
    source: CardInstance,
    event: str,
    context: Mapping[str, Any],
) -> str:
    if (
        program.event.endswith(".self")
        and str(context.get("card") or "") == source.ref
        and context.get("previous_controller") is not None
        and event in _DEPARTURE_EVENTS
    ):
        return str(context["previous_controller"])
    return source.controller


def trigger_multiplier_participations(
    host: TriggerDiscoveryHost,
    *,
    controller: str,
) -> tuple[StaticTriggerParticipation, ...]:
    """Snapshot current typed multiplier sources for one trigger controller."""

    values: list[StaticTriggerParticipation] = []
    for permanent_id in host.state.players[controller].zones["battlefield"]:
        permanent = host.state.cards[permanent_id]
        if permanent.controller != controller or permanent.phased_out:
            continue
        fragments = canonical_ability_fragments(
            host._effective_card_data(permanent).get("ability_fragments", ())
        )
        for spec in fragments:
            if not isinstance(spec, TriggerMultiplierSpec):
                continue
            chosen_type = (
                str(
                    permanent.annotations.get("chosen_creature_type") or ""
                ).strip()
                or None
            )
            try:
                values.append(
                    StaticTriggerParticipation(
                        source_object_id=permanent.object_id,
                        source_logical_object_id=permanent.logical_object_id,
                        source_controller=permanent.controller,
                        active_zone="battlefield",
                        spec=spec,
                        chosen_creature_type=chosen_type,
                    )
                )
            except ValueError as exc:
                raise GameRuleError(str(exc)) from exc
    return tuple(
        sorted(values, key=lambda value: value.fingerprint)
    )


def applicable_trigger_multipliers(
    host: TriggerDiscoveryHost,
    *,
    source: CardInstance,
    controller: str,
    event: str,
    context: Mapping[str, Any],
    participation_sources=None,
    participation_characteristics=None,
    ability_characteristics=None,
    active_zone=None,
) -> tuple[StaticTriggerParticipation, ...]:
    entering_types = {
        str(value).casefold() for value in context.get("types", [])
    }
    source_types, source_subtypes, _ = host._type_parts(
        str(host._effective_card_data(source).get("type_line") or "")
    )
    result: list[StaticTriggerParticipation] = []
    for participation in trigger_multiplier_participations(
        host,
        controller=controller,
    ):
        spec = participation.spec
        if not isinstance(spec, TriggerMultiplierSpec):
            continue
        if spec.schema_version==2:continue
        applies = False
        if (
            spec.predicate
            is TriggerMultiplierPredicate.ARTIFACT_OR_CREATURE_ENTERS
        ):
            applies = bool(
                event in _ENTER_EVENTS
                and entering_types.intersection({"artifact", "creature"})
                and source.zone == "battlefield"
            )
        elif (
            spec.predicate
            is TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE
        ):
            applies = bool(
                "creature" in source_types
                and participation.source_object_id != source.object_id
                and participation.chosen_creature_type in source_subtypes
            )
        if applies:
            result.extend([participation] * spec.additional_count)
    from .rules.trigger_participation_runtime import typed_trigger_participations
    additions,prevented=typed_trigger_participations(host,source=source,controller=controller,event=event,context=context,
        sources=participation_sources,source_characteristics=participation_characteristics,
        ability_characteristics=ability_characteristics,active_zone=active_zone or source.zone)
    if not prevented:result.extend(additions)
    else:return ()
    return tuple(result)


def additional_trigger_count(
    host: TriggerDiscoveryHost,
    *,
    source: CardInstance,
    controller: str,
    event: str,
    context: Mapping[str, Any],
) -> int:
    return 1 + len(
        applicable_trigger_multipliers(
            host,
            source=source,
            controller=controller,
            event=event,
            context=context,
        )
    )


def _uses_specialized_stack_spell_cast_owner(program: SemanticProgram) -> bool:
    """Keep intrinsic cast keywords in their dedicated occurrence owners."""

    if program.active_zone != "stack" or program.event != "spell.cast":
        return False
    return any(
        isinstance(fragment, SpellCastKeywordTriggerSpec)
        and fragment.kind
        in {
            SpellCastKeywordTriggerKind.CASCADE,
            SpellCastKeywordTriggerKind.STORM,
            SpellCastKeywordTriggerKind.COMMANDER_CAST_COPY,
            SpellCastKeywordTriggerKind.COMMANDER_CAST_COPY_NEW_TARGETS,
        }
        for fragment in fragments_from_descriptors(program.handlers)
    )


def _event_programs_for_source(
    host: TriggerDiscoveryHost,
    source: CardInstance,
    event: str,
    *,
    source_zones: Mapping[str, str] | None,
    source_characteristics: Mapping[
        str, Mapping[str, Any]
    ] | None,
) -> tuple[str, Mapping[str, Any], list[tuple[SemanticProgram, GrantingAttachmentPayment | None]]]:
    """Collect printed and individually bound granted abilities from one current source."""

    active_zone = source_zones.get(source.object_id, source.zone) if source_zones is not None else source.zone
    characteristics = (
        source_characteristics.get(source.object_id)
        if source_characteristics is not None
        else None
    ) or host._effective_card_data(source)
    if source.zone == 'command' and source.object_kind == 'emblem':
        from .emblem_programs import command_emblem_programs
        programs = command_emblem_programs(host.semantics, source,
            program_is_trusted=host.semantic_program_is_current_trusted)
        return active_zone, characteristics, [(p, None) for p in programs if p.event != 'characteristics.evaluate']
    programs = (
        []
        if source.face_down and active_zone in {"battlefield", "stack"}
        else [
            program
            for program in host.semantics.programs_for_oracle(
                copiable_oracle_id(source, characteristics),
                active_zone=event_source_zone(event, active_zone),
            )
            if not program.provenance.get("granted_only")
        ]
    )
    if not (source.face_down and active_zone in {'battlefield','stack'}):
        from .composition_characteristics import copiable_source_trigger_programs
        copied_programs=copiable_source_trigger_programs(host,characteristics,active_zone)
        if copied_programs is not None:programs=copied_programs
        elif source.permanent_composition is not None:programs=[]
    from .card_program_faces import current_shared_type_doors, room_program_face_is_active
    if current_shared_type_doors(source) is not None:
        programs = [program for program in programs if room_program_face_is_active(source, program)]
    discovered = [(program, None) for program in programs]
    for granted in granted_triggered_specs(
        canonical_ability_fragments(
            characteristics.get("ability_fragments", ())
        )
    ):
        if granted.event.removesuffix(".self") != event:
            continue
        if granted.cipher_card_ref is not None:
            from .rules.cipher import cipher_granted_program
            program = cipher_granted_program(host, source, granted)
            if program is None:
                continue
        else:
            program = host.semantics.get(granted.semantic_key)
        if program is None:
            raise GameRuleError(
                "A typed granted trigger references an unknown "
                f"semantic program {granted.semantic_key!r}"
            )
        if program.event != granted.event:
            raise GameRuleError(
                "A typed granted trigger disagrees with its semantic "
                "program event"
            )
        payment = granted.granting_attachment_payment
        if payment is not None and payment.granting_object is None:
            raise GameRuleError('A granting Equipment trigger requires its captured incarnation')
        discovered.append((program, payment))
    return active_zone, characteristics, discovered


def _semantic_trigger_context(
    host: TriggerDiscoveryHost,
    *,
    source: CardInstance,
    program: SemanticProgram,
    event: str,
    context: Mapping[str, Any],
    active_zone: str,
    source_characteristics: Mapping[str, Any],
) -> dict[str, Any]:
    from .rules.stack_damage_sources import cycled_damage_trigger_source, linked_receiving_player_context
    source, active_zone, source_characteristics = cycled_damage_trigger_source(
        host, source, program, event, active_zone, source_characteristics)
    _matches, _self_event, selected_condition = _selected_event_subscription(
        program,
        event,
    )
    stack_context = {
        "event": event,
        **copy.deepcopy(dict(context)),
        **linked_receiving_player_context(host, source, program, event),
        **event_counter_recipient_context(program,event,context),
        **trigger_attachment_context(host.state.cards, source, program),
        "source_zone": active_zone,
        "intervening_condition": (
            copy.deepcopy(dict(selected_condition))
            if isinstance(selected_condition, Mapping)
            else {}
        ),
        **_echo_control_context(source, program.event_condition),
        **(
            evolve_entrant_trigger_context(host, context)
            if _condition_mentions_field(selected_condition, EVOLVE_EVENT_CONDITION_FIELD)
            else {}
        ),
        **(
            {"intervening_condition_controller": source.controller}
            if _condition_mentions_field(
                selected_condition,
                FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
            )
            else {}
        ),
        **(
            {"trigger_target_selection_pending": True}
            if program.target_schema
            else {}
        ),
    }
    from .rules.departure_counter_transfer import DEPARTURE_COUNTER_SNAPSHOT_FIELD, DEPARTURE_COUNTER_TRANSFER_COVERAGE

    if DEPARTURE_COUNTER_TRANSFER_COVERAGE not in program.coverage:
        stack_context.pop(DEPARTURE_COUNTER_SNAPSHOT_FIELD, None)
    if 'cipher_grant' in program.provenance:
        stack_context['cipher_grant'] = copy.deepcopy(program.provenance['cipher_grant'])
    from .rules.state_triggers import state_trigger_context
    stack_context.update(state_trigger_context(source,program))
    from .rules.linked_clue_copy import linked_clue_copy_trigger_context
    stack_context.update(linked_clue_copy_trigger_context(source, program))
    from .rules.linked_exile_activations import linked_exile_activation_trigger_context

    stack_context.update(linked_exile_activation_trigger_context(source, program))
    from .effect_runtime.temporary_ability_bundle import printed_backup_source_context
    stack_context.update(trigger_occurrence_observations(host, program, event, context))
    stack_context.update(printed_backup_source_context(source,program))
    from .rules.trigger_limits import event_source_incarnation_context, event_source_usage_context
    stack_context.update(event_source_usage_context(source,program,host.state.turn_sequence))
    stack_context.update(event_source_incarnation_context(source, program))
    from .connive import connive_trigger_departure_context
    stack_context.update(connive_trigger_departure_context(host, source, program))
    if "identity_pinned_graft_counter_movement" in program.coverage:
        recipient = next((card for card in host.state.cards.values()
                          if card.ref == context.get("card")), None)
        if recipient is None:
            raise GameRuleError("Graft entry trigger lost its entering creature")
        stack_context["source_logical_object_id"] = source.logical_object_id
        stack_context["graft_recipient_identity"] = recipient.logical_object_id
    if 'identity_pinned_soulbond_pairing' in program.coverage:
        stack_context['source_logical_object_id'] = source.logical_object_id
        stack_context['intervening_condition_controller'] = source.controller
    if "one_or_more_event_batch" in program.coverage:
        from .rules.aggregated_arrival_counters import matching_arrival_member_context
        try:
            stack_context.update(matching_arrival_member_context(program, event, context))
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        aggregation_scope = (
            {"damaged_player": context.get("player")}
            if event == "damage.dealt"
            else {}
        )
        if event == 'creature.attacks':
            from .rules.attack_batches import attack_batch_aggregation_scope, declared_attack_counter_context
            try:
                aggregation_scope = attack_batch_aggregation_scope(program, context)
                stack_context.update(declared_attack_counter_context(program, source, context))
            except (ValueError, TypeError) as exc:
                raise GameRuleError(str(exc)) from exc
        stack_context["one_or_more_aggregation_id"] = hashlib.sha256(
            stable_json(
                {
                    "event": event,
                    "event_id": context.get("event_id"),
                    **aggregation_scope,
                    "source_logical_object_id": source.logical_object_id,
                    "source_ability_id": program.key,
                }
            ).encode("utf-8")
        ).hexdigest()
    if any(
        effect.get("op") == "choose_myriad_token_destinations"
        for effect in program.effects
    ):
        from .token_creation import token_copy_snapshot

        stack_context["myriad_copy_snapshot"] = token_copy_snapshot(host, source)
    if (
        isinstance(program.event_condition, Mapping)
        and program.event_condition.get("field")
        == DEATH_RETURN_EVENT_CONDITION_FIELD
    ):
        try:
            stack_context["death_return_counter_snapshot"] = dict(
                death_return_counter_snapshot(source.counters)
            )
        except DeathReturnError as exc:
            raise GameRuleError(str(exc)) from exc
    if any(
        effect.get("op") == "offer_modular_counter_transfer"
        for effect in program.effects
    ):
        try:
            counter_snapshot = modular_counter_snapshot(source.counters)
            stack_context[MODULAR_COUNTER_SNAPSHOT_FIELD] = dict(
                counter_snapshot
            )
            stack_context[MODULAR_COUNTER_COUNT_FIELD] = (
                modular_counter_count(counter_snapshot)
            )
        except ModularError as exc:
            raise GameRuleError(str(exc)) from exc
    if SPELL_HISTORY_TRANSFORM_COVERAGE in program.coverage:
        stack_context["source_transform_count"] = source.transform_count
    if "identity_pinned_exile_cast_choice" in program.coverage:
        stack_context["source_logical_object_id"] = source.logical_object_id
    from .delayed_object_actions import filter_graveyard_receipt_context
    filter_graveyard_receipt_context(event, stack_context, program)
    from .rules.trigger_source_information import trigger_result_source_information
    stack_context.update(trigger_result_source_information(host, source=source, program=program,
        event=event, stack_context=stack_context, occurrence_context=context, source_characteristics=source_characteristics, active_zone=active_zone))
    return stack_context


def trigger_multiplier_copies(
    host: TriggerDiscoveryHost,
    *,
    item: StackItem,
    source: CardInstance,
    event: str,
    context: Mapping[str, Any],
    participation_sources=None,
    participation_characteristics=None,
    ability_characteristics=None,
    active_zone=None,
) -> list[StackItem]:
    copies: list[StackItem] = []
    for copy_index, participation in enumerate(
        applicable_trigger_multipliers(
            host,
            source=source,
            controller=item.controller,
            event=event,
            context=context,
            participation_sources=participation_sources,participation_characteristics=participation_characteristics,
            ability_characteristics=ability_characteristics,active_zone=active_zone,
        ),
        start=1,
    ):
        copy_ref = host._next_ref("S")
        copied = StackItem.from_dict(item.to_dict())
        copied.stack_id = host._stable_runtime_id("stack", copy_ref)
        copied.ref = copy_ref
        copied.context = {
            **copy.deepcopy(item.context),
            "additional_trigger_copy": copy_index,
            "additional_trigger_source": participation.to_dict(),
        }
        copies.append(copied)
    return copies


def _shared_evoke_trigger_item(
    host: TriggerDiscoveryHost,
    source: CardInstance,
    event: str,
    context: Mapping[str, Any],
    *, characteristics: Mapping[str, Any],
) -> StackItem | None:
    """Materialize Evoke's universal sacrifice trigger from its cast marker."""

    if (
        event != "permanent.enter"
        or str(context.get("card") or "") != source.ref
        or source.zone != "battlefield"
        or not is_evoked_annotation(source.annotations.get("evoked"))
    ):
        return None
    evoked = source.annotations.pop("evoked", None)
    if isinstance(evoked, Mapping) and "evoke" not in normalized_characteristic_keywords(characteristics):
        return None
    ref = host._next_ref("S")
    return StackItem(
        stack_id=host._stable_runtime_id("stack", ref),
        ref=ref,
        kind="triggered_ability",
        controller=source.controller,
        label=f"{source.printed_name} evoke sacrifice",
        source_object_id=source.object_id,
        semantic_key="builtin:sacrifice-source",
        visibility=list(host.seats),
        context={
            "event": event,
            "event_context": copy.deepcopy(dict(context)),
            "source_logical_object_id": source.logical_object_id,
            "evoke": True,
            **({"evoke_source_identity_version": 2} if isinstance(evoked, Mapping) else {}),
        },
    )


def _shared_blitz_trigger_item(
    host: TriggerDiscoveryHost,
    source: CardInstance,
    event: str,
    context: Mapping[str, Any],
    *,
    characteristics: Mapping[str, Any],
) -> StackItem | None:
    """Materialize Blitz's incarnation-pinned graveyard trigger from LKI."""

    marker = source.annotations.get("fixed_blitz_designation")
    schema_version = (
        marker.get("schema_version", 1)
        if isinstance(marker, Mapping)
        else None
    )
    legacy = schema_version == 1
    if (
        event != ("creature.dies" if legacy else "permanent.graveyard")
        or str(context.get("card") or "") != source.ref
        or not isinstance(marker, Mapping)
        or marker.get("logical_object_id") != source.logical_object_id
        or type(marker.get("controller")) is not str
    ):
        return None
    if not legacy:
        ability_key = marker.get("ability_semantic_key")
        program = (
            host.semantics.get(ability_key)
            if type(ability_key) is str
            else None
        )
        if (
            schema_version != 2
            or program is None
            or program.oracle_id != source.oracle_id
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or not program_has_current_ability_fragments(
                program,
                characteristics,
            )
        ):
            return None
    controller = str(context.get("previous_controller") or source.controller)
    if controller not in host.active_seats:
        return None
    ref = host._next_ref("S")
    return StackItem(
        stack_id=host._stable_runtime_id("stack", ref),
        ref=ref,
        kind="triggered_ability",
        controller=controller,
        label=f"{source.printed_name} Blitz draw",
        source_object_id=source.object_id,
        visibility=list(host.seats),
        context={
            "event": event,
            "event_context": copy.deepcopy(dict(context)),
            "source_logical_object_id": source.logical_object_id,
            "dynamic_effects": [
                {"op": "draw", "player": controller, "count": 1}
            ],
            "blitz": True,
        },
    )


def _ordered_event_sources(host, sources, source_zones=None):
    from .zone_trigger_processing import order_observing_trigger_sources
    return order_observing_trigger_sources(host, sources, source_zones)


def _granted_prowess_trigger_items(host, source, event, context, *, active_zone, characteristics, programs,
        participation_sources=None,participation_characteristics=None):
    """Materialize granted instances without duplicating printed Prowess programs."""
    if (event != 'spell.cast' or active_zone != 'battlefield' or source.phased_out
            or context.get('controller') != source.controller
            or 'creature' in {str(t).casefold() for t in context.get('types', ())}):
        return ()
    fragments = canonical_ability_fragments(characteristics.get('ability_fragments', ()))
    total = sum(isinstance(f, SpellCastKeywordTriggerSpec) and f.kind is SpellCastKeywordTriggerKind.PROWESS for f in fragments)
    printed = sum(
        program.provenance.get('template_id') == 'prowess-noncreature-spell-trigger-v1'
        and program_has_current_ability_fragments(program, characteristics)
        for program, _ in programs
    )
    result = []
    for instance in range(max(0, total-printed)):
        ref = host._next_ref('S')
        item = StackItem(
            stack_id=host._stable_runtime_id('stack', ref), ref=ref,
            kind='triggered_ability', controller=source.controller,
            label=source.printed_name+' granted Prowess', source_object_id=source.object_id,
            visibility=list(host.seats), context={
                'event':event,'event_context':copy.deepcopy(dict(context)),
                'source_logical_object_id':source.logical_object_id,'granted_prowess_instance':instance,
                'dynamic_effects':[{'op':'modify_stats_until_end_of_turn','card':'$source.zone_object','power':1,'toughness':1}],
            },
        )
        result.append(item)
        result.extend(trigger_multiplier_copies(host, item=item, source=source, event=event, context=context,
            participation_sources=participation_sources,participation_characteristics=participation_characteristics,
            ability_characteristics=characteristics,active_zone=active_zone))
    return tuple(result)


def _trigger_program_is_current_trusted(host, program):
    if 'cipher_grant' in program.provenance:
        from .rules.cipher import cipher_program_is_current_trusted
        return cipher_program_is_current_trusted(host, program)
    return host.semantic_program_is_current_trusted(program)


def _program_trigger_ability_is_present(program, characteristics):
    if ('identity_pinned_champion' in program.coverage
            and 'champion' not in {str(keyword).casefold() for keyword in characteristics.get('keywords', ())}):
        return False
    if ('identity_pinned_soulbond_pairing' in program.coverage
            and 'soulbond' not in {str(keyword).casefold() for keyword in characteristics.get('keywords', ())}):
        return False
    return (CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or program_has_current_ability_fragments(program, characteristics))


def _new_semantic_trigger_item(host, *, source, program, event, context, active_zone, characteristics,
                               departure_characteristics=None, granting_attachment_payment=None):
    """Construct one occurrence with source-pinned context before batching."""
    ref = host._next_ref("S")
    stack_context = _semantic_trigger_context(
        host, source=source, program=program, event=event, context=context,
        active_zone=active_zone, source_characteristics=characteristics,
    )
    if granting_attachment_payment is not None:
        stack_context[GRANTING_ATTACHMENT_PAYMENT_FIELD] = granting_attachment_payment.to_dict()
    from .rules.event_counter_threshold import initial_event_counter_threshold
    stack_context.update(initial_event_counter_threshold(program.effects, source=source,
        current=host.state.cards.get(source.object_id)))
    from .drawing.miracle_triggers import miracle_trigger_context
    from .rules.controlled_destruction_source_counters import creature_role_trigger_context
    from .reflexive_trigger_model import bound_reflexive_has_targets
    from .relative_power_target import bind_relative_mana_value_trigger_schema
    from .target_source import BOUND_TARGETING_SOURCE_VERSION, capture_targeting_source, TARGETING_SOURCE_CONTEXT
    try:
        stack_context.update(creature_role_trigger_context(program.target_schema, host, source))
        stack_context.update(bind_relative_mana_value_trigger_schema(
            host, source, program.target_schema,
            death_context=context if event == "creature.dies" else None,
            source_characteristics=characteristics,
        ))
        from .rules.destruction_result_model import NEXT_END_STEP_DESTRUCTION_FIELD
        if (bound_reflexive_has_targets(program.effects)
            or any(NEXT_END_STEP_DESTRUCTION_FIELD in effect for effect in program.effects)):
            stack_context[TARGETING_SOURCE_CONTEXT] = capture_targeting_source(
                host, source, characteristics=characteristics, zone=active_zone, schema_version=BOUND_TARGETING_SOURCE_VERSION,
            ).to_dict()
        stack_context.update(occurrence_power_trigger_context(
            host, program, event, context, departure_characteristics,
        ))
        stack_context.update(miracle_trigger_context(
            source=source, program=program, event=event, context=context, trigger_ref=ref,
        ))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return StackItem(
        stack_id=host._stable_runtime_id("stack", ref), ref=ref, kind="triggered_ability",
        controller=_trigger_controller(program, source, event, context), label=program.label,
        source_object_id=source.object_id, semantic_key=program.key,
        visibility=list(host.seats), context=stack_context,
    )


def _source_may_observe_event(source, event, programs):
    """Reject only sources with neither a subscription nor a shared keyword path."""
    if (event == 'spell.cast' or is_evoked_annotation(source.annotations.get('evoked'))
            or source.annotations.get('fixed_blitz_designation') is not None):
        return True
    return any(
        program.trust_level == 'unresolved' or program.event_condition is not None
        or not isinstance(program.event, str) or program.event.removesuffix('.self') == event
        for program, _ in programs
    )


def dispatch_semantic_event(
    host: TriggerDiscoveryHost,
    event: str,
    context: Mapping[str, Any],
    *,
    sources: Sequence[CardInstance] | None = None,
    source_zones: Mapping[str, str] | None = None,
    source_characteristics: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
    trigger_batch: list[StackItem] | None = None,
    played_land_reference: str | None = None,
    target_announcement_subtypes: tuple[str, ...] | None = None,
) -> list[str]:
    """Detect data-driven triggers for one normalized authoritative event."""

    # Keep discovery-only facts out of recorded event envelopes.
    discovery_context = context
    if event == LAND_PLAY_EVENT and played_land_reference is not None:
        discovery_context = {**context, PLAYED_LAND_REFERENCE_FIELD: played_land_reference}
    elif event == TARGET_ANNOUNCEMENT_EVENT:
        discovery_context = {**context, "subtypes": target_announcement_subtypes or ()}
    triggered: list[StackItem] = []
    participation_sources = _ordered_event_sources(host, sources, source_zones)
    def read_programs(source):
        return _event_programs_for_source(
            host,source,event,source_zones=source_zones,source_characteristics=source_characteristics)

    if event in {"card.drawn", "card.second_draw", DRAW_EXCEPT_FIRST_OWN_STEP_EVENT}:
        # Draw observers use the same committed state. Keep this read before any
        # trigger usage, pending control duration, or stack writes.
        with characteristic_read_frame(host):
            source_programs = [(source, read_programs(source)) for source in participation_sources]
    else:
        source_programs = ((source, read_programs(source)) for source in participation_sources)
    for source, (active_zone, characteristics, programs) in source_programs:
        if not _source_may_observe_event(source, event, programs):
            continue
        from .rules.trigger_participation_runtime import typed_trigger_participations
        _extra,prevented=typed_trigger_participations(host,source=source,controller=source.controller,event=event,context=context,
            sources=participation_sources,source_characteristics=source_characteristics,
            ability_characteristics=characteristics,active_zone=active_zone)
        if prevented:continue
        evoke_item = _shared_evoke_trigger_item(host, source, event, context, characteristics=characteristics)
        if evoke_item is not None:
            triggered.append(evoke_item)
            triggered.extend(
                trigger_multiplier_copies(
                    host,
                    item=evoke_item,
                    source=source,
                    event=event,
                    context=context,
                    participation_sources=participation_sources,participation_characteristics=source_characteristics,
                    ability_characteristics=characteristics,active_zone=active_zone,
                )
            )
        triggered.extend(_granted_prowess_trigger_items(host, source, event, context,
            active_zone=active_zone, characteristics=characteristics, programs=programs,
            participation_sources=participation_sources,participation_characteristics=source_characteristics))
        blitz_item = _shared_blitz_trigger_item(
            host,
            source,
            event,
            context,
            characteristics=characteristics,
        )
        if blitz_item is not None:
            triggered.append(blitz_item)
            triggered.extend(
                trigger_multiplier_copies(
                    host,
                    item=blitz_item,
                    source=source,
                    event=event,
                    context=context,
                    participation_sources=participation_sources,participation_characteristics=source_characteristics,
                    ability_characteristics=characteristics,active_zone=active_zone,
                )
            )
        for program, granting_attachment_payment in programs:
            if _uses_specialized_stack_spell_cast_owner(program):
                continue
            if program.trust_level == "unresolved":
                continue
            if not _program_trigger_ability_is_present(program, characteristics):
                continue
            from .rules.state_triggers import state_trigger_available
            if not state_trigger_available(host,source,program,trigger_batch):continue
            if not semantic_event_matches(
                host,
                program,
                source,
                event,
                discovery_context,
                source_zone=active_zone,
            ):
                continue
            if event == 'creature.blocks' and block_trigger_already_collected(program.key,
                    source.object_id, context, _selected_event_subscription(program, event)[2], trigger_batch):
                continue
            if (
                host.state.config.semantic_policy == "trusted_only"
                and not _trigger_program_is_current_trusted(host, program)
            ):
                host._pause_for_unsupported_semantic(
                    program=program,
                    event=event,
                    source=source,
                )
                return [item.ref for item in triggered]
            if program.trigger_limit is not None:
                from .rules.trigger_limits import trigger_usage_plan
                try:usage=trigger_usage_plan(host.state,source,program,(*(trigger_batch or ()),*triggered))
                except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
                if usage is None:continue
            item = _new_semantic_trigger_item(
                host, source=source, program=program, event=event, context=context,
                active_zone=active_zone, characteristics=characteristics,
                departure_characteristics=source_characteristics,
                granting_attachment_payment=granting_attachment_payment,
            )
            if trigger_batch is not None and "one_or_more_event_batch" in program.coverage:
                from .rules.aggregated_arrival_counters import coalesce_trigger_aggregation

                try:
                    if coalesce_trigger_aggregation(item, trigger_batch):
                        continue
                except ValueError as exc:
                    raise GameRuleError(str(exc)) from exc
            from .control_effects import pin_pending_control_duration
            pin_pending_control_duration(host, item)
            if program.trigger_limit is not None and usage.live_source is not None:
                commit_trigger_usage(usage.live_source, ability_id=program.ability_id,
                                     turn_sequence=host.state.turn_sequence)
            triggered.append(item)
            if program.trigger_limit is None or usage.allows_multipliers:
                triggered.extend(
                    trigger_multiplier_copies(
                        host,
                        item=item,
                        source=source,
                        event=event,
                        context=context,
                        participation_sources=participation_sources,participation_characteristics=source_characteristics,
                        ability_characteristics=characteristics,active_zone=active_zone,
                    )
                )
            if "consume_evoked_marker" in program.coverage:
                source.annotations.pop("evoked", None)
    if trigger_batch is not None:
        trigger_batch.extend(triggered)
    elif triggered:
        enqueue_trigger_batch(host, triggered)
    return [item.ref for item in triggered]


__all__ = [
    "TriggerDiscoveryHost",
    "additional_trigger_count",
    "applicable_trigger_multipliers",
    "dispatch_semantic_event",
    "semantic_event_condition_matches",
    "semantic_intervening_condition_matches",
    "semantic_event_matches",
    "semantic_event_value",
    "trigger_multiplier_participations",
    "trigger_multiplier_copies",
]
