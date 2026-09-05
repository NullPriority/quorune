from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Any, Mapping, Protocol, Sequence

from ..card_program_faces import program_matches_face, selected_face_id
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..commander_zones import (
    commander_hand_library_replacement_effect,
    CommanderZoneError,
)
from ..entry_counters import (
    EntryCounterError,
    EffectEntryCounter,
    effect_entry_counter_effects,
    intrinsic_entry_counter_effects,
    intrinsic_entry_counters,
)
from ..entry_keyword_grants import (
    EntryKeywordGrant,
)
from ..entry_state_metrics import (
    controller_basic_land_types,
    entry_condition_metrics,
)
from ..entry_state_conditions import FIXED_ENTRY_CONDITION_HANDLER_ID
from ..replacement_effects import (
    AffectedObject,
    CreateAffectedObjectCounter,
    ReplaceableEvent,
    ReplacementBatchChoice,
    ReplacementClass,
    ReplacementEffect,
    ReplacementEventBatch,
    ReplacementSelection,
    ReplacementChoiceRequired,
    advance_replacement_batch,
    replacement_choice,
)
from ..rules.capabilities import load_default_capability_registry
from ..turn_history import opponent_was_dealt_damage_this_turn
from ..flashback import flashed_back_subject_replacements
from ..unearth import unearthed_leave_replacement
from ..zone_trigger_events import ZoneTransitionKind
from ..kicker import KICKER_ANNOTATION
from ..madness import MADNESS_REPLACEMENT_HANDLER_ID
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .counter_replacements import (
    collect_counter_placement_replacement_effects,
)
from .self_entry_counters import SelfEntryCounterHandler
from .conditional_entry_counters import ConditionalSelfEntryCounterHandler
from .sunburst import SunburstEntryCounterHandler
from .entry_choices import ReadAheadEntryChoiceHandler, RiotEntryChoiceHandler
from .kicker import FixedKickedEntryHandler
from .madness import MadnessDiscardReplacementHandler
from ..read_ahead import READ_AHEAD_ENTRY_HANDLER_ID
from .entry_state import (
    ENTRY_STATE_HANDLER_ID,
    EntryStateReplacementHandler,
    FixedEntryConditionReplacementHandler,
)
from .zone_replacement_model import (
    PreparedZoneChange,
    SUPPORTED_ZONE_DESTINATIONS,
    ZoneChangeReplacementContext,
    ZoneChangeReplacementResolution,
    ZoneChangeReplacementSnapshot,
    ZoneChangeSubjectSnapshot,
    ZoneDestinationIntent,
    ZoneDestinationReplacementNode,
    ZoneReplacementError,
)
from .zone_replacement_inputs import validated_zone_change_snapshot_inputs


_DESTINATION_HANDLER_ID = "replacement.zone.destination.v1"
_COUNTERS_FIELD = "counters"


class ZoneReplacementHost(Protocol):
    state: Any
    semantics: Any
    card_db: Any

    @property
    def active_seats(self) -> list[str]: ...

    def _semantic_event_sources(
        self, *, zones: set[str]
    ) -> Sequence[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _effective_static_component_keys(
        self, card: Any
    ) -> tuple[str, ...]: ...

    def card_record(self, card: Any) -> Any: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ZoneDestinationReplacementHandler:
    handler_id: str = _DESTINATION_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.destination"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "400.6",
        "614.1",
        "614.1a",
        "614.5",
        "616.1",
        "616.1f",
        "616.2",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.change.destination_replacement",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> ZoneDestinationReplacementNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "destination",
                _COUNTERS_FIELD,
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Runtime handler ID does not match registry")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(f"{self.handler_id} must handle {self.event}")
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError("runtime handler condition must be an object")
        exact_fields(
            condition,
            {"destination", "object_kind", "owner_relation"},
            field="runtime handler condition",
        )
        destination = str(condition["destination"] or "")
        object_kind = str(condition["object_kind"] or "")
        owner_relation = str(condition["owner_relation"] or "")
        replacement_destination = str(descriptor["destination"] or "")
        if (
            destination not in SUPPORTED_ZONE_DESTINATIONS
            or replacement_destination not in SUPPORTED_ZONE_DESTINATIONS
        ):
            raise SemanticNodeError(
                "Zone destination replacement requires supported game zones"
            )
        if object_kind != "card":
            raise SemanticNodeError(
                "Zone destination replacement currently requires object_kind=card"
            )
        if owner_relation != "opponent":
            raise SemanticNodeError(
                "Zone destination replacement currently requires "
                "owner_relation=opponent"
            )
        counters_value = descriptor[_COUNTERS_FIELD]
        if not isinstance(counters_value, Mapping):
            raise SemanticNodeError("replacement counters must be an object")
        counters: list[tuple[str, int]] = []
        for raw_name, raw_amount in counters_value.items():
            name = " ".join(str(raw_name).casefold().split())
            if (
                not name
                or type(raw_amount) is not int
                or int(raw_amount) < 1
            ):
                raise SemanticNodeError(
                    "replacement counters require positive integer amounts"
                )
            counters.append((name, int(raw_amount)))
        return ZoneDestinationReplacementNode(
            destination=destination,
            object_kind=object_kind,
            owner_relation=owner_relation,
            replacement_destination=replacement_destination,
            counters=tuple(sorted(counters)),
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        node = self.validate(descriptor)
        if (
            context.destination != node.destination
            or not context.is_card_object
            or context.object_owner == context.source_controller
        ):
            return ()
        return (
            ZoneDestinationIntent(
                handler_id=self.handler_id,
                source_ref=context.source_ref,
                destination=node.replacement_destination,
                counters=node.counters,
            ),
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        return self._source_replacement_effect(
            node,
            source_ref=context.source_ref,
            source_controller=context.source_controller,
            component_id=(
                context.component_id or node.replacement_destination
            ),
        )

    def source_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        source_ref: str,
        source_controller: str,
        component_id: str,
    ) -> ReplacementEffect:
        return self._source_replacement_effect(
            self.validate(descriptor),
            source_ref=source_ref,
            source_controller=source_controller,
            component_id=component_id,
        )

    def _source_replacement_effect(
        self,
        node: ZoneDestinationReplacementNode,
        *,
        source_ref: str,
        source_controller: str,
        component_id: str,
    ) -> ReplacementEffect:
        if not source_ref or not source_controller or not component_id:
            raise SemanticNodeError(
                "Zone replacement sources require stable identity"
            )
        operations: list[Mapping[str, Any]] = [
            {
                "op": "set",
                "field": "destination",
                "value": node.replacement_destination,
            }
        ]
        for index, (name, amount) in enumerate(node.counters):
            operations.append(
                CreateAffectedObjectCounter(
                    counter_name=name,
                    amount=amount,
                    placing_player=source_controller,
                    source_ref=source_ref,
                    sequence=index,
                )
            )
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{source_ref}:{component_id}"
            ),
            source_id=source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": node.destination},
                "object_kind": {"eq": node.object_kind},
                "owner": {"not_in": [source_controller]},
            },
            operations=tuple(operations),
            label=(
                f"{source_ref}: put the card into "
                f"{node.replacement_destination} instead"
            ),
        )


class ZoneChangeReplacementRegistry(
    RuntimeComponentRegistry[
        ZoneChangeReplacementContext,
        ZoneDestinationIntent,
    ]
):
    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> ReplacementEffect:
        handler = self._handler(descriptor)
        compiler = getattr(handler, "replacement_effect", None)
        if compiler is None:
            raise SemanticNodeError(
                f"Runtime handler {handler.handler_id} cannot compile a "
                "replacement effect"
            )
        return compiler(descriptor, context)

    def source_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        source_ref: str,
        source_controller: str,
        component_id: str,
    ) -> ReplacementEffect:
        handler = self._handler(descriptor)
        compiler = getattr(handler, "source_replacement_effect", None)
        if compiler is None:
            raise SemanticNodeError(
                f"Runtime handler {handler.handler_id} cannot compile a "
                "source replacement effect"
            )
        return compiler(
            descriptor,
            source_ref=source_ref,
            source_controller=source_controller,
            component_id=component_id,
        )

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> ReplacementEffect | None:
        handler = self._handler(descriptor)
        compiler = getattr(handler, "subject_replacement_effect", None)
        if compiler is None:
            raise SemanticNodeError(
                f"Runtime handler {handler.handler_id} cannot compile an "
                "affected-object replacement effect"
            )
        return compiler(
            descriptor,
            subject=subject,
            component_id=component_id,
        )

    def subject_replacement_effects(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> tuple[ReplacementEffect, ...]:
        handler = self._handler(descriptor)
        compiler = getattr(handler, "subject_replacement_effects", None)
        if compiler is not None:
            effects = tuple(
                compiler(
                    descriptor,
                    subject=subject,
                    component_id=component_id,
                )
            )
            if any(
                not isinstance(effect, ReplacementEffect)
                for effect in effects
            ):
                raise SemanticNodeError(
                    "Affected-object replacement compilation must be typed"
                )
            return effects
        effect = self.subject_replacement_effect(
            descriptor,
            subject=subject,
            component_id=component_id,
        )
        return () if effect is None else (effect,)


@lru_cache(maxsize=1)
def default_zone_change_replacement_registry(
) -> ZoneChangeReplacementRegistry:
    registry = ZoneChangeReplacementRegistry(
        (
            ConditionalSelfEntryCounterHandler(),
            EntryStateReplacementHandler(),
            FixedEntryConditionReplacementHandler(),
            ReadAheadEntryChoiceHandler(),
            RiotEntryChoiceHandler(),
            FixedKickedEntryHandler(),
            MadnessDiscardReplacementHandler(),
            SelfEntryCounterHandler(),
            SunburstEntryCounterHandler(),
            ZoneDestinationReplacementHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def _zone_change_programs_for_record(
    host: ZoneReplacementHost,
    record: Any,
    *,
    active_zone: str,
) -> tuple[Any, ...]:
    """Return trusted programs plus the narrow historical-v3 entry adapter.

    A current registry must contain its compiler output and never reaches the
    adapter.  Historical Game Record v3 snapshots did not serialize the new
    entry-state handler, so replay may compile only that exact handler family
    from the record's pinned card database when the snapshot compatibility bit
    is enabled.
    """

    programs = tuple(
        program
        for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone=active_zone,
            event="zone.change",
        )
        if host.semantic_program_is_current_trusted(program)
    )
    if any(
        descriptor.get("handler_id") == ENTRY_STATE_HANDLER_ID
        for program in programs
        for descriptor in program.handlers
    ):
        return programs
    if not host.semantics.runtime_handler_compatibility_enabled:
        return programs

    # This import is intentionally operation-local.  It is a replay adapter,
    # not a second runtime Oracle interpretation path for current games.
    from ..oracle_ir import generated_programs

    try:
        generated = generated_programs(
            host.card_db,
            record,
            trust_level="trusted",
            capability_registry=load_default_capability_registry(),
        )
    except ValueError:
        return programs
    entry_programs = tuple(
        program
        for program in generated
        if program.active_zone == active_zone
        and program.event == "zone.change"
        and any(
            descriptor.get("handler_id") == ENTRY_STATE_HANDLER_ID
            for descriptor in program.handlers
        )
    )
    return (*programs, *entry_programs)


def collect_zone_change_replacement_effects(
    host: ZoneReplacementHost,
    *,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
) -> tuple[ReplacementEffect, ...]:
    """Compile trusted ambient zone replacements without card dispatch.

    The returned effects contain only source semantics.  Affected-object facts
    are bound later by the immutable event snapshot, so one effect can safely
    participate in every event of a simultaneous batch.
    """

    candidates = (
        list(sources)
        if sources is not None
        else host._semantic_event_sources(zones={"battlefield"})
    )
    registry = default_zone_change_replacement_registry()
    effects: list[ReplacementEffect] = []
    for source in candidates:
        active_zone = (
            source_zones.get(source.object_id, source.zone)
            if source_zones is not None
            else source.zone
        )
        if (
            active_zone != "battlefield"
            or source.phased_out
            or source.controller not in host.active_seats
        ):
            continue
        unearth_replacement = unearthed_leave_replacement(source)
        if unearth_replacement is not None:
            effects.append(unearth_replacement)
        record = host.card_record(source)
        if record is None:
            continue
        programs = _zone_change_programs_for_record(
            host,
            record,
            active_zone="battlefield",
        )
        for program in programs:
            if not program_matches_face(record, program, source):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                effects.append(
                    registry.source_replacement_effect(
                        descriptor,
                        source_ref=source.ref,
                        source_controller=source.controller,
                        component_id=f"{program.key}:{descriptor_index}",
                    )
                )
    effects.extend(
        collect_counter_placement_replacement_effects(
            host,
            sources=candidates,
            source_zones=source_zones,
        )
    )
    return tuple(effects)


def typed_entry_life_payment_amount(
    host: ZoneReplacementHost,
    record: Any,
    *,
    card: Any | None = None,
    prospective_name: str | None = None,
) -> int:
    """Return the source-pinned optional entry payment for one exact face."""

    handler = EntryStateReplacementHandler()
    amounts: list[int] = []
    programs = _zone_change_programs_for_record(
        host,
        record,
        active_zone="all",
    )
    for program in programs:
        if not program_matches_face(
            record,
            program,
            card,
            prospective_name=prospective_name,
        ):
            continue
        for descriptor in program.handlers:
            if descriptor.get("handler_id") != ENTRY_STATE_HANDLER_ID:
                continue
            amount = handler.optional_life_amount(descriptor)
            if amount:
                amounts.append(amount)
    if len(amounts) > 1:
        raise SemanticNodeError(
            "One selected face cannot authorize multiple entry life payments"
        )
    return amounts[0] if amounts else 0


def _read_ahead_entry_is_supported(
    host: ZoneReplacementHost,
    record: Any | None,
    card: Any,
    *,
    prospective_name: str,
    characteristics: Mapping[str, Any],
    subtypes: set[str],
) -> bool:
    keywords = {
        " ".join(str(value).casefold().split())
        for value in characteristics.get("keywords") or ()
    }
    if "read ahead" not in keywords:
        return False
    if "saga" not in subtypes:
        raise SemanticNodeError("Read Ahead entry requires a Saga")
    if record is None:
        raise SemanticNodeError(
            "Read Ahead entry requires a pinned card record"
        )
    if not card.is_card_object or card.annotations.get("copy_overrides"):
        raise SemanticNodeError(
            "Copied or granted Read Ahead is outside the represented boundary"
        )

    handler = ReadAheadEntryChoiceHandler()
    nodes = []
    for program in _zone_change_programs_for_record(
        host,
        record,
        active_zone="all",
    ):
        if not program_matches_face(
            record,
            program,
            card,
            prospective_name=prospective_name or None,
        ):
            continue
        for descriptor in program.handlers:
            if descriptor.get("handler_id") == READ_AHEAD_ENTRY_HANDLER_ID:
                nodes.append(handler.validate(descriptor))
    if len(nodes) != 1:
        raise SemanticNodeError(
            "Read Ahead entry requires one trusted typed handler"
        )

    represented: set[int] = set()
    for program in host.semantics.programs_for_oracle(record.oracle_id):
        match = re.fullmatch(
            r"saga\.chapter\.(?P<number>[1-9]\d*)",
            str(program.event or ""),
        )
        if (
            match is None
            or not host.semantic_program_is_current_trusted(program)
            or not program_matches_face(
                record,
                program,
                card,
                prospective_name=prospective_name or None,
            )
        ):
            continue
        represented.add(int(match.group("number")))
    if tuple(sorted(represented)) != nodes[0].chapter_numbers:
        raise SemanticNodeError(
            "Read Ahead entry requires matching trusted typed chapter programs"
        )
    return True


def _fixed_entry_condition_metrics(
    host: ZoneReplacementHost,
    *,
    record: Any,
    card: Any,
    destination: str,
    destination_controller: str | None,
    prospective_name: str,
    cache: dict[str | None, Mapping[str, int]],
) -> Mapping[str, int]:
    if destination != "battlefield" or record is None:
        return {}
    uses_fixed_condition = any(
        descriptor.get("handler_id") == FIXED_ENTRY_CONDITION_HANDLER_ID
        for program in _zone_change_programs_for_record(
            host,
            record,
            active_zone="all",
        )
        if program_matches_face(
            record,
            program,
            card,
            prospective_name=prospective_name or None,
        )
        for descriptor in program.handlers
    )
    if not uses_fixed_condition:
        return {}
    if destination_controller not in cache:
        cache[destination_controller] = entry_condition_metrics(
            host,
            destination_controller,
        )
    return cache[destination_controller]


def _zone_change_snapshot_subjects(
    host: ZoneReplacementHost,
    changes: Sequence[tuple[str, str]],
    *,
    destination_controllers: Mapping[str, str | None],
    entry_characteristics: Mapping[str, Mapping[str, Any]],
    effect_entry_counters: Mapping[str, Sequence[EffectEntryCounter]],
    mana_colors_spent: Mapping[str, Sequence[str]],
    requested_tapped: Mapping[str, bool],
    entry_pay_life: Mapping[str, bool | None],
    transition_kinds: Mapping[str, ZoneTransitionKind],
    error_type: type[Exception],
) -> tuple[ZoneChangeSubjectSnapshot, ...]:
    subjects: list[ZoneChangeSubjectSnapshot] = []
    entry_metrics_by_controller: dict[str | None, Mapping[str, int]] = {}
    for object_id, destination in changes:
        card = host.state.cards.get(object_id)
        if card is None:
            raise error_type(
                "Zone replacement snapshot references an unknown object"
            )
        try:
            characteristics = dict(
                entry_characteristics.get(
                    object_id, host._effective_card_data(card)
                )
            )
            card_types, subtypes, supertypes = host._type_parts(
                str(characteristics.get("type_line") or "")
            )
            destination_controller = (
                destination_controllers[object_id]
                if object_id in destination_controllers
                else card.controller if card.zone == "stack" else card.owner
            )
            controlled_basic_types = controller_basic_land_types(
                host,
                destination_controller,
            )
            record = host.card_record(card)
            prospective_name = str(characteristics.get("name") or "")
            read_ahead_supported = _read_ahead_entry_is_supported(
                host,
                record,
                card,
                prospective_name=prospective_name,
                characteristics=characteristics,
                subtypes=subtypes,
            )
            subjects.append(
                ZoneChangeSubjectSnapshot(
                    object_id=card.object_id,
                    object_ref=card.ref,
                    logical_object_id=card.logical_object_id,
                    owner=card.owner,
                    controller=(
                        card.controller
                        if card.zone in {"battlefield", "stack"}
                        else None
                    ),
                    origin=card.zone,
                    destination=destination,
                    destination_controller=destination_controller,
                    entry_face_id=(
                        selected_face_id(
                            record,
                            card,
                            prospective_name=prospective_name or None,
                        )
                        if record is not None
                        else "front"
                    ),
                    opponent_was_dealt_damage_this_turn=(
                        opponent_was_dealt_damage_this_turn(
                            host.state.turn_history,
                            turn_sequence=host.state.turn_sequence,
                            player=destination_controller,
                            active_players=host.active_seats,
                        )
                        if destination_controller is not None
                        else False
                    ),
                    mana_colors_spent=tuple(
                        mana_colors_spent.get(card.object_id, ())
                    ),
                    intrinsic_entry_counters=intrinsic_entry_counters(
                        characteristics,
                        card_types=tuple(sorted(card_types)),
                        card_subtypes=tuple(sorted(subtypes)),
                        keywords=tuple(characteristics.get("keywords") or ()),
                        read_ahead_supported=read_ahead_supported,
                    ),
                    effect_entry_counters=tuple(
                        effect_entry_counters.get(card.object_id, ())
                    ),
                    requested_tapped=bool(
                        requested_tapped.get(card.object_id, False)
                    ),
                    entry_pay_life=entry_pay_life.get(card.object_id, False),
                    opponent_count=(
                        sum(
                            1
                            for seat in host.active_seats
                            if seat != destination_controller
                        )
                        if destination_controller is not None
                        else 0
                    ),
                    controller_basic_land_types=controlled_basic_types,
                    entry_condition_metrics=_fixed_entry_condition_metrics(
                        host,
                        record=record,
                        card=card,
                        destination=destination,
                        destination_controller=destination_controller,
                        prospective_name=prospective_name,
                        cache=entry_metrics_by_controller,
                    ),
                    object_types=tuple(
                        sorted({*card_types, *subtypes, *supertypes})
                    ),
                    is_card_object=card.is_card_object,
                    transition_kind=transition_kinds.get(
                        card.object_id,
                        ZoneTransitionKind.ORDINARY,
                    ),
                    is_commander=bool(card.is_commander),
                    commander_designation_id=card.commander_designation_id,
                    cast_option=(
                        "kicked"
                        if card.annotations.get(KICKER_ANNOTATION) is True
                        else None
                    ),
                )
            )
        except (
            EntryCounterError,
            SemanticNodeError,
            ZoneReplacementError,
        ) as exc:
            raise error_type(str(exc)) from exc
    return tuple(subjects)


def _active_zone_replacement_sources(
    host: ZoneReplacementHost,
    *,
    sources: Sequence[Any] | None,
    source_zones: Mapping[str, str] | None,
) -> tuple[Any, ...]:
    candidates = (
        tuple(sources)
        if sources is not None
        else tuple(host._semantic_event_sources(zones={"battlefield"}))
    )
    return tuple(
        source
        for source in candidates
        if (
            (
                source_zones.get(source.object_id, source.zone)
                if source_zones is not None
                else source.zone
            )
            == "battlefield"
            and not source.phased_out
            and source.controller in host.active_seats
        )
    )


def _zone_change_snapshot_effects(
    host: ZoneReplacementHost,
    subjects: Sequence[ZoneChangeSubjectSnapshot],
    active_sources: Sequence[Any],
) -> tuple[ReplacementEffect, ...]:
    ambient_effects = collect_zone_change_replacement_effects(
        host,
        sources=active_sources, source_zones={source.object_id: "battlefield" for source in active_sources},
    )
    intrinsic_effects = tuple(
        effect
        for subject in subjects
        if subject.destination_controller is not None
        for effect in intrinsic_entry_counter_effects(
            object_ref=subject.object_ref,
            destination_controller=subject.destination_controller,
            counters=subject.intrinsic_entry_counters,
        )
    )
    generated_effects = tuple(
        effect
        for subject in subjects
        for effect in effect_entry_counter_effects(
            object_ref=subject.object_ref, counters=subject.effect_entry_counters,
        )
    )
    registry = default_zone_change_replacement_registry()
    self_entry_effects: list[ReplacementEffect] = []
    subject_effects: list[ReplacementEffect] = []
    for subject in subjects:
        card = host.state.cards.get(subject.object_id)
        if card is None:
            raise ZoneReplacementError(
                "Affected zone-replacement source disappeared during snapshot"
            )
        record = host.card_record(card)
        if record is None:
            continue
        for program in _zone_change_programs_for_record(
            host,
            record,
            active_zone="all",
        ):
            if (
                not program_matches_face(record, program, card)
                or (
                    CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                    and program.key
                    not in host._effective_static_component_keys(card)
                )
            ):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                if (
                    descriptor.get("handler_id")
                    != MADNESS_REPLACEMENT_HANDLER_ID
                ):
                    continue
                subject_effects.extend(
                    registry.subject_replacement_effects(
                        descriptor,
                        subject=subject,
                        component_id=f"{program.key}:{descriptor_index}",
                    )
                )
        if subject.destination != "battlefield":
            continue
        programs = _zone_change_programs_for_record(
            host,
            record,
            active_zone="all",
        )
        for program in programs:
            if not program_matches_face(
                record,
                program,
                card,
                prospective_name=(
                    subject.entry_face_id
                    if subject.entry_face_id != "front"
                    else None
                ),
            ):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                self_entry_effects.extend(
                    registry.subject_replacement_effects(
                        descriptor,
                        subject=subject,
                        component_id=f"{program.key}:{descriptor_index}",
                    )
                )
    try:
        commander_effects = tuple(
            effect
            for subject in subjects
            for effect in (
                commander_hand_library_replacement_effect(subject),
            )
            if effect is not None
        )
    except CommanderZoneError as exc:
        raise ZoneReplacementError(str(exc)) from exc
    return tuple(
        sorted(
            (
                *ambient_effects,
                *flashed_back_subject_replacements(host.state.cards, (subject.object_id for subject in subjects)),
                *intrinsic_effects,
                *generated_effects,
                *self_entry_effects,
                *subject_effects,
                *commander_effects,
            ),
            key=lambda effect: effect.effect_id,
        )
    )


def capture_zone_change_replacement_snapshot(
    host: ZoneReplacementHost,
    changes: Sequence[tuple[str, str]],
    *,
    destination_controllers: Mapping[str, str | None] | None = None,
    entry_characteristics: Mapping[str, Mapping[str, Any]] | None = None,
    effect_entry_counters: Mapping[
        str, Sequence[EffectEntryCounter]
    ] | None = None,
    mana_colors_spent: Mapping[str, Sequence[str]] | None = None,
    requested_tapped: Mapping[str, bool] | None = None,
    entry_pay_life: Mapping[str, bool | None] | None = None,
    transition_kinds: Mapping[str, ZoneTransitionKind] | None = None,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
    error_type: type[Exception] = ZoneReplacementError,
) -> ZoneChangeReplacementSnapshot:
    """Capture every represented source and affected object before mutation."""

    (
        supplied,
        controllers,
        characteristics,
        effect_counters,
        cast_colors,
        tapped_requests,
        life_choices,
        kinds,
    ) = validated_zone_change_snapshot_inputs(
        host,
        changes,
        destination_controllers=destination_controllers,
        entry_characteristics=entry_characteristics,
        effect_entry_counters=effect_entry_counters,
        mana_colors_spent=mana_colors_spent,
        requested_tapped=requested_tapped,
        entry_pay_life=entry_pay_life,
        transition_kinds=transition_kinds,
        error_type=error_type,
    )
    subjects = _zone_change_snapshot_subjects(
        host,
        supplied,
        destination_controllers=controllers,
        entry_characteristics=characteristics,
        effect_entry_counters=effect_counters,
        mana_colors_spent=cast_colors,
        requested_tapped=tapped_requests,
        entry_pay_life=life_choices,
        transition_kinds=kinds,
        error_type=error_type,
    )
    active_sources = _active_zone_replacement_sources(
        host,
        sources=sources,
        source_zones=source_zones,
    )
    try:
        return ZoneChangeReplacementSnapshot(
            revision=host.state.revision,
            event_sequence=host.state.event_sequence,
            apnap_order=tuple(host.apnap_order()),
            source_refs=tuple(source.ref for source in active_sources),
            subjects=subjects,
            effects=_zone_change_snapshot_effects(
                host, subjects, active_sources
            ),
        )
    except (SemanticNodeError, ZoneReplacementError) as exc:
        raise error_type(str(exc)) from exc


def _snapshot_event(
    snapshot: ZoneChangeReplacementSnapshot,
    subject: ZoneChangeSubjectSnapshot,
) -> ReplaceableEvent:
    return ReplaceableEvent(
        event_id=(
            f"zone.change:{snapshot.revision}:"
            f"{snapshot.event_sequence + 1}:{subject.object_ref}"
        ),
        kind="zone.change",
        affected_player=None,
        affected_object=AffectedObject(
            object_id=subject.object_id,
            owner=subject.owner,
            controller=(
                subject.owner
                if subject.is_commander
                and subject.destination in {"hand", "library"}
                else (
                    subject.destination_controller
                    if subject.destination == "battlefield"
                    else subject.controller
                )
            ),
        ),
        payload={
            "origin": subject.origin,
            "destination": subject.destination,
            "destination_controller": subject.destination_controller,
            "object_kind": "card" if subject.is_card_object else "noncard",
            "object_ref": subject.object_ref,
            "object_types": list(subject.object_types),
            "logical_object_id": subject.logical_object_id,
            "transition_kind": subject.transition_kind.value,
            "owner": subject.owner,
            **(
                {"cast_option": subject.cast_option}
                if subject.cast_option is not None
                else {}
            ),
            "tapped": subject.requested_tapped,
            "entry_life_payment": 0,
            "read_ahead_chapter": None,
            "opponent_count": subject.opponent_count,
            "controller_basic_land_types": list(
                subject.controller_basic_land_types
            ),
            "opponent_was_dealt_damage_this_turn": (
                subject.opponent_was_dealt_damage_this_turn
            ),
        },
    )


def _prepared_from_event(
    subject: ZoneChangeSubjectSnapshot,
    event: ReplaceableEvent,
    *,
    state_revision: int,
    event_sequence: int,
    effects: tuple[ReplacementEffect, ...],
    journal: tuple[ReplacementSelection, ...],
) -> PreparedZoneChange:
    counter_events: list[ReplaceableEvent] = []

    def visit(current: ReplaceableEvent) -> None:
        if current.kind == "counter.place":
            counter_events.append(current)
        for child in current.children:
            visit(child)

    visit(event)
    raw_grants = event.payload.get("entry_keyword_grants", ())
    if not isinstance(raw_grants, (list, tuple)):
        raise ZoneReplacementError("Entry keyword grants must be an array")
    keyword_grants: list[EntryKeywordGrant] = []
    for value in raw_grants:
        if not isinstance(value, Mapping) or set(value) != {
            "effect_id",
            "keyword",
            "sequence",
        }:
            raise ZoneReplacementError(
                "Entry keyword grants require exact typed fields"
            )
        keyword_grants.append(
            EntryKeywordGrant(
                effect_id=value["effect_id"],
                keyword=value["keyword"],
                sequence=value["sequence"],
            )
        )
    entry_tapped = event.payload.get("tapped")
    entry_life_payment = event.payload.get("entry_life_payment")
    read_ahead_chapter = event.payload.get("read_ahead_chapter")
    if type(entry_tapped) is not bool:
        raise ZoneReplacementError(
            "Prepared zone entry tapped state must be boolean"
        )
    if (
        type(entry_life_payment) is not int
        or entry_life_payment < 0
    ):
        raise ZoneReplacementError(
            "Prepared zone entry life payment must be nonnegative"
        )
    if read_ahead_chapter is not None and (
        type(read_ahead_chapter) is not int or read_ahead_chapter < 1
    ):
        raise ZoneReplacementError(
            "Prepared Read Ahead chapter must be a positive integer or null"
        )
    return PreparedZoneChange(
        object_id=subject.object_id,
        logical_object_id=subject.logical_object_id,
        origin=subject.origin,
        requested_destination=subject.destination,
        destination_controller=subject.destination_controller,
        entry_face_id=subject.entry_face_id,
        state_revision=state_revision,
        event_sequence=event_sequence,
        destination=str(event.payload["destination"]),
        transition_kind=subject.transition_kind,
        requested_tapped=subject.requested_tapped,
        requested_entry_pay_life=subject.entry_pay_life,
        entry_tapped=entry_tapped,
        entry_life_payment=entry_life_payment,
        read_ahead_chapter=read_ahead_chapter,
        event=event,
        effects=effects,
        counter_events=tuple(counter_events),
        keyword_grants=tuple(sorted(keyword_grants)),
        journal=journal,
    )


def prepare_zone_change_replacement(
    host: ZoneReplacementHost,
    card: Any,
    destination: str,
    *,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
    destination_controller: str | None = None,
    entry_characteristics: Mapping[str, Any] | None = None,
    effect_entry_counters: Sequence[EffectEntryCounter] = (),
    mana_colors_spent: Sequence[str] = (),
    requested_tapped: bool = False,
    entry_pay_life: bool = False,
    transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    prepared: PreparedZoneChange | None = None,
    error_type: type[Exception] = ZoneReplacementError,
) -> PreparedZoneChange:
    """Resolve ambient destination replacements before a zone mutation."""

    if prepared is not None:
        record = host.card_record(card)
        prospective_name = str(
            (entry_characteristics or {}).get("name") or ""
        )
        entry_face_id = (
            selected_face_id(
                record,
                card,
                prospective_name=prospective_name or None,
            )
            if record is not None
            else "front"
        )
        effective_destination_controller = (
            destination_controller
            if destination_controller is not None
            else card.controller if card.zone == "stack" else card.owner
        )
        if (
            prepared.object_id != card.object_id
            or prepared.logical_object_id != card.logical_object_id
            or prepared.origin != card.zone
            or prepared.requested_destination != destination
            or prepared.destination_controller
            != effective_destination_controller
            or prepared.entry_face_id != entry_face_id
            or prepared.state_revision != host.state.revision
            or prepared.requested_tapped is not requested_tapped
            or prepared.requested_entry_pay_life is not entry_pay_life
            or prepared.transition_kind is not transition_kind
        ):
            raise error_type(
                "Prepared zone replacement does not match the proposed move"
            )
        if selections:
            raise error_type(
                "Replacement selections cannot modify a prepared zone move"
            )
        return prepared
    return prepare_zone_change_replacement_batch(
        host,
        ((card.object_id, destination),),
        destination_controllers=(
            {card.object_id: destination_controller}
            if destination_controller is not None
            else None
        ),
        entry_characteristics=(
            {card.object_id: entry_characteristics}
            if entry_characteristics is not None
            else None
        ),
        effect_entry_counters=(
            {card.object_id: tuple(effect_entry_counters)}
            if effect_entry_counters
            else None
        ),
        mana_colors_spent=(
            {card.object_id: tuple(mana_colors_spent)}
            if mana_colors_spent
            else None
        ),
        requested_tapped={card.object_id: requested_tapped},
        entry_pay_life={card.object_id: entry_pay_life},
        transition_kinds={card.object_id: transition_kind},
        sources=sources,
        source_zones=source_zones,
        selections=selections,
        error_type=error_type,
    )[card.object_id]


def prepare_zone_change_replacement_batch(
    host: ZoneReplacementHost,
    changes: Sequence[tuple[str, str]],
    *,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
    destination_controllers: Mapping[str, str | None] | None = None,
    entry_characteristics: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
    effect_entry_counters: Mapping[
        str, Sequence[EffectEntryCounter]
    ] | None = None,
    mana_colors_spent: Mapping[str, Sequence[str]] | None = None,
    requested_tapped: Mapping[str, bool] | None = None,
    entry_pay_life: Mapping[str, bool | None] | None = None,
    transition_kinds: Mapping[str, ZoneTransitionKind] | None = None,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    error_type: type[Exception] = ZoneReplacementError,
) -> dict[str, PreparedZoneChange]:
    """Resolve one immutable simultaneous batch before mutating any object."""

    snapshot = capture_zone_change_replacement_snapshot(
        host,
        changes,
        destination_controllers=destination_controllers,
        entry_characteristics=entry_characteristics,
        effect_entry_counters=effect_entry_counters,
        mana_colors_spent=mana_colors_spent,
        requested_tapped=requested_tapped,
        entry_pay_life=entry_pay_life,
        transition_kinds=transition_kinds,
        sources=sources,
        source_zones=source_zones,
        error_type=error_type,
    )
    return prepare_zone_change_replacement_snapshot(
        snapshot,
        selections=selections,
        error_type=error_type,
    )


def prepare_zone_change_replacement_snapshot(
    snapshot: ZoneChangeReplacementSnapshot,
    *,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    error_type: type[Exception] = ZoneReplacementError,
) -> dict[str, PreparedZoneChange]:
    """Resolve a captured batch without consulting mutable game state."""

    if not isinstance(snapshot, ZoneChangeReplacementSnapshot):
        raise error_type(
            "Zone replacement preparation requires an immutable snapshot"
        )
    events = tuple(
        _snapshot_event(snapshot, subject) for subject in snapshot.subjects
    )
    applicable = tuple(
        (subject, event)
        for subject, event in zip(snapshot.subjects, events, strict=True)
        if replacement_choice(event, snapshot.effects) is not None
    )
    if not applicable:
        if selections:
            raise error_type(
                "Replacement selections were supplied without an applicable "
                "zone-change replacement"
            )
        return {
            subject.object_id: _prepared_from_event(
                subject,
                event,
                state_revision=snapshot.revision,
                event_sequence=snapshot.event_sequence,
                effects=snapshot.effects,
                journal=(),
            )
            for subject, event in zip(snapshot.subjects, events, strict=True)
        }
    applicable_subjects = tuple(subject for subject, _event in applicable)
    applicable_events = tuple(event for _subject, event in applicable)
    progress = advance_replacement_batch(
        ReplacementEventBatch(
            batch_id=(
                f"replacement:zone.batch:{snapshot.revision}:"
                f"{snapshot.event_sequence + 1}"
            ),
            events=applicable_events,
            apnap_order=snapshot.apnap_order,
        ),
        snapshot.effects,
        selections=tuple(selections),
    )
    if progress.pending is not None:
        raise ReplacementChoiceRequired(
            batch=progress.batch,
            effects=snapshot.effects,
            pending=progress.pending,
        )
    prepared: dict[str, PreparedZoneChange] = {
        subject.object_id: _prepared_from_event(
            subject,
            event,
            state_revision=snapshot.revision,
            event_sequence=snapshot.event_sequence,
            effects=snapshot.effects,
            journal=(),
        )
        for subject, event in zip(snapshot.subjects, events, strict=True)
    }
    for subject, event in zip(
        applicable_subjects,
        progress.batch.events,
        strict=True,
    ):
        event_journal = tuple(
            selection
            for selection in progress.batch.journal
            if selection.event_id == event.event_id
        )
        prepared[subject.object_id] = _prepared_from_event(
            subject,
            event,
            state_revision=snapshot.revision,
            event_sequence=snapshot.event_sequence,
            effects=snapshot.effects,
            journal=event_journal,
        )
    return prepared


def log_applied_zone_replacements(
    host: ZoneReplacementHost,
    prepared: PreparedZoneChange,
    card: Any,
    *,
    requested_destination: str,
    error_type: type[Exception],
) -> None:
    """Emit public audit events from a committed replacement journal."""

    effect_by_id = {
        effect.effect_id: effect for effect in prepared.effects
    }
    for selection in prepared.journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        replacement = effect_by_id.get(selected_id)
        if replacement is None:
            raise error_type(
                "Applied zone replacement is absent from its source snapshot"
            )
        if replacement.event_kind != "zone.change":
            continue
        host._log(
            None,
            "replacement.apply",
            (
                f"{replacement.source_id} replaced the zone change for "
                f"{card.ref}."
            ),
            {
                "source": replacement.source_id,
                "effect_id": replacement.effect_id,
                "object": card.ref,
                "replaced_destination": requested_destination,
                "destination": card.zone,
                _COUNTERS_FIELD: [
                    {
                        "name": str(
                            event.payload.get("counter_name") or ""
                        ),
                        "amount": int(event.payload.get("amount", 0)),
                    }
                    for event in prepared.counter_events
                    if event.payload.get("source")
                    == replacement.source_id
                ],
            },
            importance=2,
            changed_objects=[card.object_id],
        )


def resolve_zone_change_replacements(
    *,
    event_id: str,
    object_id: str,
    owner: str,
    controller: str | None,
    origin: str,
    destination: str,
    is_card_object: bool,
    effects: Sequence[ReplacementEffect],
    apnap_order: Sequence[str],
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    object_ref: str | None = None,
    logical_object_id: str | None = None,
    object_types: Sequence[str] = (),
    destination_controller: str | None = None,
) -> ZoneChangeReplacementResolution:
    event = ReplaceableEvent(
        event_id=event_id,
        kind="zone.change",
        affected_player=None,
        affected_object=AffectedObject(
            object_id=object_id,
            owner=owner,
            controller=controller,
        ),
        payload={
            "origin": origin,
            "destination": destination,
            "destination_controller": (
                destination_controller
                if destination_controller is not None
                else controller
            ),
            "object_kind": "card" if is_card_object else "noncard",
            "object_ref": object_ref or object_id,
            "object_types": sorted(set(object_types)),
            "logical_object_id": logical_object_id or object_id,
            "owner": owner,
        },
    )
    progress = advance_replacement_batch(
        ReplacementEventBatch(
            batch_id=f"replacement:{event_id}",
            events=(event,),
            apnap_order=tuple(apnap_order),
        ),
        tuple(effects),
        selections=tuple(selections),
    )
    resolved_event = progress.batch.events[0]
    return ZoneChangeReplacementResolution(
        batch=progress.batch,
        event=resolved_event,
        effects=tuple(effects),
        journal=progress.batch.journal,
        pending=progress.pending,
    )
