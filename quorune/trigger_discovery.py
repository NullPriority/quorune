from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    canonical_ability_fragments,
    granted_triggered_specs,
)
from .attachment_references import (
    SourceAttachmentSnapshot,
    capture_last_known_attachment_snapshot,
    capture_source_attachment_snapshot,
    required_attachment_relation,
)
from .errors import GameRuleError
from .evolve import (
    EVOLVE_EVENT_CONDITION_FIELD,
    EvolveCharacteristics,
    evolve_condition_holds,
)
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
from .semantic_runtime.ability_fragments import fragments_from_descriptors
from .trigger_processing import enqueue_trigger_batch
from .trigger_participation import (
    StaticTriggerParticipation,
    TriggerMultiplierPredicate,
    TriggerMultiplierSpec,
)
from .util import stable_json


_DEPARTURE_EVENTS = frozenset(
    {
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

def _trigger_attachment_snapshot(
    host: "TriggerDiscoveryHost",
    source: CardInstance,
    program: SemanticProgram,
) -> SourceAttachmentSnapshot | None:
    relation = required_attachment_relation(program.effects)
    if relation is None:
        return None
    authoritative = host.state.cards.get(source.object_id)
    if authoritative is source:
        return capture_source_attachment_snapshot(
            host.state.cards,
            source,
            relation,
        )
    attached = host.state.cards.get(source.attached_to or "")
    return capture_last_known_attachment_snapshot(
        host.state.cards,
        source,
        relation,
        source_logical_object_id=source.logical_object_id,
        attached_to_ref=attached.ref if attached is not None else None,
    )


def _trigger_attachment_context(
    host: "TriggerDiscoveryHost",
    source: CardInstance,
    program: SemanticProgram,
) -> dict[str, Any]:
    snapshot = _trigger_attachment_snapshot(host, source, program)
    if snapshot is None:
        return {"source_logical_object_id": source.logical_object_id}
    return {
        "source_logical_object_id": snapshot.source.logical_object_id,
        "source_attachment_snapshot": snapshot.to_dict(),
    }


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

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _resolve_object(
        self, actor: str, ref: str, *, zones: set[str]
    ) -> CardInstance: ...

    def _numeric_stat(self, object_id: str, stat: str) -> int: ...

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
    substitutions = {
        "$source.controller": source.controller,
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

    if field == RENOWN_EVENT_CONDITION_FIELD:
        try:
            return renown_condition_holds(source, context)
        except RenownError as exc:
            raise GameRuleError(str(exc)) from exc
    if field != EVOLVE_EVENT_CONDITION_FIELD:
        raise GameRuleError(
            f"Unsupported typed keyword condition field {field!r}"
        )
    expected_source_identity = context.get("source_logical_object_id")
    resolving = expected_source_identity is not None
    if resolving and (
        type(expected_source_identity) is not str
        or not expected_source_identity
    ):
        raise GameRuleError(
            "Evolve source logical identity must be a nonempty string"
        )
    if (
        source.zone != "battlefield"
        or source.phased_out
        or (
            resolving
            and source.logical_object_id != expected_source_identity
        )
    ):
        return False
    entered_ref = context.get("card")
    entered_incarnation = context.get("card_zone_change_counter")
    if type(entered_ref) is not str or not entered_ref:
        raise GameRuleError("Evolve event requires an entered card ref")
    if type(entered_incarnation) is not int or entered_incarnation < 0:
        raise GameRuleError(
            "Evolve event requires a nonnegative zone-change counter"
        )
    entered = next(
        (
            card
            for card in host.state.cards.values()
            if card.ref == entered_ref
        ),
        None,
    )
    if (
        entered is None
        or entered.zone != "battlefield"
        or entered.phased_out
        or entered.zone_change_counter != entered_incarnation
    ):
        return False
    if not resolving and context.get("controller") != source.controller:
        return False
    source_types, _, _ = host._type_parts(
        str(host._effective_card_data(source).get("type_line") or "")
    )
    entered_types, _, _ = host._type_parts(
        str(host._effective_card_data(entered).get("type_line") or "")
    )
    return evolve_condition_holds(
        EvolveCharacteristics(
            is_creature="creature" in source_types,
            power=host._numeric_stat(source.object_id, "power"),
            toughness=host._numeric_stat(source.object_id, "toughness"),
        ),
        EvolveCharacteristics(
            is_creature="creature" in entered_types,
            power=host._numeric_stat(entered.object_id, "power"),
            toughness=host._numeric_stat(entered.object_id, "toughness"),
        ),
    )


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
    if field in {EVOLVE_EVENT_CONDITION_FIELD, RENOWN_EVENT_CONDITION_FIELD}:
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
    if field.startswith("source_annotation."):
        return source.annotations.get(field.removeprefix("source_annotation."))
    if field == "source_active_face":
        if source.active_face:
            return source.active_face
        record = host.card_record(source)
        if record is not None and record.faces:
            return str(record.faces[0].get("name") or "")
        return host._effective_card_data(source).get("name")
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


def semantic_event_matches(
    host: TriggerDiscoveryHost,
    program: SemanticProgram,
    source: CardInstance,
    event: str,
    context: Mapping[str, Any],
    *,
    source_zone: str | None = None,
) -> bool:
    self_event = program.event.endswith(".self")
    program_event = (
        program.event.removesuffix(".self") if self_event else program.event
    )
    if program_event != event or program.active_zone != (
        source_zone or source.zone
    ):
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
    if program.event_condition is not None:
        return semantic_event_condition_matches(
            host,
            program.event_condition,
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


def program_has_current_ability_fragments(
    program: SemanticProgram,
    characteristics: Mapping[str, Any],
) -> bool:
    """Require every typed fragment declared by one current-ability program."""

    required = fragments_from_descriptors(program.handlers)
    if not required:
        raise GameRuleError(
            "A current-ability trigger has no typed ability fragment"
        )
    available = list(
        canonical_ability_fragments(
            characteristics.get("ability_fragments", ())
        )
    )
    for fragment in required:
        try:
            available.remove(fragment)
        except ValueError:
            return False
    return True


def _event_programs_for_source(
    host: TriggerDiscoveryHost,
    source: CardInstance,
    event: str,
    *,
    source_zones: Mapping[str, str] | None,
    source_characteristics: Mapping[
        str, Mapping[str, Any]
    ] | None,
) -> tuple[str, Mapping[str, Any], list[SemanticProgram]]:
    """Collect printed and typed-granted programs from one current source."""

    active_zone = (
        source_zones.get(source.object_id, source.zone)
        if source_zones is not None
        else source.zone
    )
    characteristics = (
        source_characteristics.get(source.object_id)
        if source_characteristics is not None
        else None
    ) or host._effective_card_data(source)
    programs = (
        []
        if source.face_down and active_zone in {"battlefield", "stack"}
        else [
            program
            for program in host.semantics.programs_for_oracle(
                source.oracle_id,
                active_zone=active_zone,
            )
            if not program.provenance.get("granted_only")
        ]
    )
    for granted in granted_triggered_specs(
        canonical_ability_fragments(
            characteristics.get("ability_fragments", ())
        )
    ):
        if granted.event != event:
            continue
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
        programs.append(program)
    return active_zone, characteristics, programs


def _semantic_trigger_context(
    host: TriggerDiscoveryHost,
    *,
    source: CardInstance,
    program: SemanticProgram,
    event: str,
    context: Mapping[str, Any],
    active_zone: str,
) -> dict[str, Any]:
    stack_context = {
        "event": event,
        **copy.deepcopy(dict(context)),
        **_trigger_attachment_context(host, source, program),
        "source_zone": active_zone,
        "intervening_condition": (
            copy.deepcopy(dict(program.event_condition))
            if isinstance(program.event_condition, Mapping)
            else {}
        ),
        **_echo_control_context(source, program.event_condition),
        **(
            {"trigger_target_selection_pending": True}
            if program.target_schema
            else {}
        ),
    }
    if "one_or_more_event_batch" in program.coverage:
        stack_context["one_or_more_aggregation_id"] = hashlib.sha256(
            stable_json(
                {
                    "event": event,
                    "event_id": context.get("event_id"),
                    "source_logical_object_id": source.logical_object_id,
                    "source_ability_id": program.key,
                }
            ).encode("utf-8")
        ).hexdigest()
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
    return stack_context


def _trigger_multiplier_copies(
    host: TriggerDiscoveryHost,
    *,
    item: StackItem,
    source: CardInstance,
    event: str,
    context: Mapping[str, Any],
) -> list[StackItem]:
    copies: list[StackItem] = []
    for copy_index, participation in enumerate(
        applicable_trigger_multipliers(
            host,
            source=source,
            controller=item.controller,
            event=event,
            context=context,
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
) -> StackItem | None:
    """Materialize Evoke's universal sacrifice trigger from its cast marker."""

    if (
        event != "permanent.enter"
        or str(context.get("card") or "") != source.ref
        or source.zone != "battlefield"
        or source.annotations.get("evoked") is not True
    ):
        return None
    source.annotations.pop("evoked", None)
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
        },
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
) -> list[str]:
    """Detect data-driven triggers for one normalized authoritative event."""

    triggered: list[StackItem] = []
    candidates = (
        list(sources) if sources is not None else host._semantic_event_sources()
    )
    for source in candidates:
        evoke_item = _shared_evoke_trigger_item(host, source, event, context)
        if evoke_item is not None:
            triggered.append(evoke_item)
            triggered.extend(
                _trigger_multiplier_copies(
                    host,
                    item=evoke_item,
                    source=source,
                    event=event,
                    context=context,
                )
            )
        active_zone, characteristics, programs = _event_programs_for_source(
            host,
            source,
            event,
            source_zones=source_zones,
            source_characteristics=source_characteristics,
        )
        for program in programs:
            if program.trust_level == "unresolved":
                continue
            if (
                CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                and not program_has_current_ability_fragments(
                    program,
                    characteristics,
                )
            ):
                continue
            if not semantic_event_matches(
                host,
                program,
                source,
                event,
                context,
                source_zone=active_zone,
            ):
                continue
            if (
                host.state.config.semantic_policy == "trusted_only"
                and not host.semantic_program_is_current_trusted(program)
            ):
                host._pause_for_unsupported_semantic(
                    program=program,
                    event=event,
                    source=source,
                )
                return [item.ref for item in triggered]
            ref = host._next_ref("S")
            stack_context = _semantic_trigger_context(
                host,
                source=source,
                program=program,
                event=event,
                context=context,
                active_zone=active_zone,
            )
            item = StackItem(
                stack_id=host._stable_runtime_id("stack", ref),
                ref=ref,
                kind="triggered_ability",
                controller=_trigger_controller(program, source, event, context),
                label=program.label,
                source_object_id=source.object_id,
                semantic_key=program.key,
                visibility=list(host.seats),
                context=stack_context,
            )
            if (
                trigger_batch is not None
                and "one_or_more_event_batch" in program.coverage
                and any(
                    existing.semantic_key == item.semantic_key
                    and existing.source_object_id == item.source_object_id
                    and existing.context.get("event") == event
                    for existing in trigger_batch
                )
            ):
                continue
            triggered.append(item)
            triggered.extend(
                _trigger_multiplier_copies(
                    host,
                    item=item,
                    source=source,
                    event=event,
                    context=context,
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
    "semantic_event_matches",
    "semantic_event_value",
    "trigger_multiplier_participations",
]
