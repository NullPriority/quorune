from __future__ import annotations

from ..card_program_faces import program_matches_face
from ..entry_state_conditions import FIXED_ENTRY_CONDITION_HANDLER_ID, FixedEntryMetric
from ..entry_state_metrics import EntryConditionMetricHost, controller_active_turn_number, entry_condition_metrics

"""Closed input validation for immutable zone-replacement snapshots."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from typing import Any, Mapping, Sequence

from ..ability_fragment_primitives import current_saga_intrinsic_abilities
from ..entry_counters import EffectEntryCounter
from ..zone_trigger_events import ZoneTransitionKind
from ..replacement.entry_counter_types import ENTRY_TIME_COUNTER_TYPES_FIELD
from ..replacement_effects import AffectedObject, EntryReplacementScope, ReplaceableEvent, ReplacementEffect
from .context import SemanticNodeError
from .entry_designations import CountedCreatureTypeEntryDesignationHandler, EntryOpponentDesignationHandler
from .zone_replacement_model import ZoneChangeReplacementSnapshot, ZoneChangeSubjectSnapshot


def zone_change_subject_card(
    host: Any,
    object_id: str,
    prospective_objects: Mapping[str, Any],
    error_type: type[Exception],
) -> Any:
    """Resolve an existing or explicitly prospective immutable subject."""
    card = prospective_objects.get(object_id) or host.state.cards.get(object_id)
    if card is None:
        raise error_type("Zone replacement snapshot references an unknown object")
    return card


def active_zone_replacement_sources(
    host: Any,
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


def prospective_destination_controller(
    card: Any,
    destination_controllers: Mapping[str, str | None],
) -> str | None:
    return destination_controllers.get(
        card.object_id,
        card.controller if card.zone == "stack" else card.owner,
    )


def zone_change_subject_characteristics(host, card, destination, entry_characteristics, destination_controllers):
    """Prepare one immutable prospective subject before replacement discovery."""
    if card.object_id in entry_characteristics:
        characteristics = dict(entry_characteristics[card.object_id])
    else:
        characteristics = dict(host._effective_card_data(card))
    controller = prospective_destination_controller(card, destination_controllers)
    if destination == 'battlefield':
        from .fixed_characteristic_settings import prospective_creature_type_characteristics
        characteristics = prospective_creature_type_characteristics(host, card, characteristics,
            destination_controller=controller)
    card_types, subtypes, supertypes = host._type_parts(str(characteristics.get('type_line') or ''))
    if destination == "battlefield" and "saga" in subtypes and current_saga_intrinsic_abilities(host.state):
        from ..ability_fragment_primitives import INTRINSIC_ABILITIES_REMOVED
        from ..card_program_faces import prospective_face_view

        entrant = prospective_face_view(host.card_record(card), card, characteristics.get("name"))
        from ..ability_fragment_primitives import current_saga_read_ahead_grants
        from ..continuous_effects import Layer
        from ..keyword_abilities import normalized_characteristic_keywords
        from ..read_ahead import GRANTED_READ_AHEAD_ENTRY

        _, prospective = _entry_layer_characteristics(host, entrant, controller, maximum_layer=Layer.ABILITY)
        if prospective.get(INTRINSIC_ABILITIES_REMOVED) is True:
            characteristics[INTRINSIC_ABILITIES_REMOVED] = True
        if (current_saga_read_ahead_grants(host.state)
                and "read ahead" not in normalized_characteristic_keywords(characteristics)
                and "read ahead" in normalized_characteristic_keywords(prospective)):
            characteristics[GRANTED_READ_AHEAD_ENTRY] = True
            characteristics["keywords"] = (*characteristics.get("keywords", ()), "Read Ahead")
    return characteristics, controller, card_types, subtypes, supertypes


def _entry_layer_characteristics(host, card, controller, *, maximum_layer, copy_values=None, entry_face=None):
    from ..entry_copy import COPIABLE_FACE_FIELD
    from ..zone_object_state import reset_card_after_zone_change

    preview = deepcopy(card)
    reset_card_after_zone_change(preview, destination="battlefield", stack_to_battlefield=False)
    preview = replace(
        preview, zone="battlefield", controller=controller,
        zone_change_counter=preview.zone_change_counter + 1,
    )
    if entry_face is not None:
        preview = replace(preview, active_face=entry_face)
    if copy_values is not None:
        preview = replace(
            preview, active_face=copy_values.get(COPIABLE_FACE_FIELD),
            annotations={**preview.annotations, "copy_overrides": dict(copy_values)},
        )
    return preview, host._effective_card_data(preview, maximum_layer=maximum_layer)


def prospective_entry_loses_intrinsic_abilities(host, card, controller, *, copy_values=None):
    from ..ability_fragment_primitives import INTRINSIC_ABILITIES_REMOVED
    from ..continuous_effects import Layer

    _, data = _entry_layer_characteristics(
        host, card, controller, maximum_layer=Layer.ABILITY, copy_values=copy_values,
    )
    if copy_values is not None:
        from ..ability_fragment_primitives import current_saga_read_ahead_grants
        from ..keyword_abilities import normalized_characteristic_keywords

        if (current_saga_read_ahead_grants(host.state)
                and "read ahead" in normalized_characteristic_keywords(data)
                and "read ahead" not in normalized_characteristic_keywords(copy_values)):
            raise SemanticNodeError("Granted Read Ahead on a Saga entry copy requires a represented copy plan")
    return data.get(INTRINSIC_ABILITIES_REMOVED) is True


def conditional_counter_entry_types(host, card, controller, *, copy_values=None, entry_face=None):
    """Read the entrant's type layer without adding it to the battlefield."""
    from ..continuous_effects import Layer
    from .fixed_characteristic_settings import prospective_creature_type_characteristics

    preview, data = _entry_layer_characteristics(
        host, card, controller, maximum_layer=Layer.TYPE, copy_values=copy_values, entry_face=entry_face,
    )
    data = prospective_creature_type_characteristics(
        host, preview, data, destination_controller=controller, include_controlled_subtypes=False,
    )
    return tuple(sorted(set().union(*host._type_parts(str(data["type_line"])))))


def subject_entry_counter_fields(host, card, destination, controller, characteristics, type_parts, counters):
    """Capture type facts used by entry-counter instructions and their replacements."""
    from .impending import prospective_impending_entry_types

    tags = (
        conditional_counter_entry_types(host, card, controller)
        if any(counter.required_entry_type is not None for counter in counters)
        else tuple(sorted(set().union(*type_parts)))
    )
    return {
        "object_types": tags,
        "entry_time_counter_types": prospective_impending_entry_types(
            host, card, destination, controller, characteristics, tags, counters,
        ),
    }


def validated_zone_change_snapshot_inputs(
    host: Any,
    changes: Sequence[tuple[str, str]],
    *,
    destination_controllers: Mapping[str, str | None] | None,
    entry_characteristics: Mapping[str, Mapping[str, Any]] | None,
    effect_entry_counters: Mapping[
        str, Sequence[EffectEntryCounter]
    ] | None,
    mana_colors_spent: Mapping[str, Sequence[str]] | None,
    requested_tapped: Mapping[str, bool] | None,
    entry_pay_life: Mapping[str, bool | None] | None,
    transition_kinds: Mapping[str, ZoneTransitionKind] | None,
    error_type: type[Exception],
) -> tuple[
    tuple[tuple[str, str], ...],
    Mapping[str, str | None],
    Mapping[str, Mapping[str, Any]],
    Mapping[str, Sequence[EffectEntryCounter]],
    Mapping[str, Sequence[str]],
    Mapping[str, bool],
    Mapping[str, bool | None],
    Mapping[str, ZoneTransitionKind],
]:
    supplied = tuple(changes)
    if any(
        not isinstance(change, tuple)
        or len(change) != 2
        or any(type(value) is not str or not value for value in change)
        for change in supplied
    ):
        raise error_type(
            "Zone replacement snapshots require object and destination pairs"
        )
    object_ids = tuple(object_id for object_id, _destination in supplied)
    if len(object_ids) != len(set(object_ids)):
        raise error_type("Zone replacement snapshots cannot repeat one object")

    controllers = destination_controllers or {}
    characteristics = entry_characteristics or {}
    effect_counters = effect_entry_counters or {}
    cast_colors = mana_colors_spent or {}
    tapped_requests = requested_tapped or {}
    life_choices = entry_pay_life or {}
    kinds = transition_kinds or {}
    keyed_inputs = (
        (controllers, "destination controllers"),
        (characteristics, "entry characteristics"),
        (effect_counters, "effect entry counters"),
        (cast_colors, "cast colors"),
        (tapped_requests, "tapped requests"),
        (life_choices, "entry life choices"),
        (kinds, "transition kinds"),
    )
    for values, label in keyed_inputs:
        if set(values) - set(object_ids):
            raise error_type(
                f"Zone replacement {label} reference unknown objects"
            )
    if any(not isinstance(value, Mapping) for value in characteristics.values()):
        raise error_type(
            "Zone replacement entry characteristics must be mappings"
        )
    if any(
        not isinstance(values, (list, tuple))
        or any(type(value) is not str or value not in "WUBRG" for value in values)
        or len(values) != len(set(values))
        for values in cast_colors.values()
    ):
        raise error_type(
            "Zone replacement cast colors must be distinct WUBRG sequences"
        )
    if any(type(value) is not bool for value in tapped_requests.values()):
        raise error_type("Zone replacement tapped requests must be booleans")
    if any(
        value is not None and type(value) is not bool
        for value in life_choices.values()
    ):
        raise error_type(
            "Zone replacement entry life choices must be booleans or null"
        )
    if any(
        not isinstance(value, ZoneTransitionKind) for value in kinds.values()
    ):
        raise error_type("Zone replacement transition kinds must be typed")
    if any(
        not isinstance(values, (list, tuple))
        or any(not isinstance(value, EffectEntryCounter) for value in values)
        for values in effect_counters.values()
    ):
        raise error_type(
            "Zone replacement effect entry counters must be typed sequences"
        )
    if any(
        counter.placing_player not in host.active_seats
        for values in effect_counters.values()
        for counter in values
    ):
        raise error_type(
            "Zone replacement effect entry counter player is not active"
        )
    return (
        supplied,
        controllers,
        characteristics,
        effect_counters,
        cast_colors,
        tapped_requests,
        life_choices,
        kinds,
    )


def _zone_change_snapshot_event_id(snapshot, subject):
    from ..replacement.counted_entry_counters import ENTRY_COUNTER_TRANSFER_OPERATIONS

    if any(isinstance(operation, ENTRY_COUNTER_TRANSFER_OPERATIONS)
           for effect in snapshot.effects for operation in effect.operations):
        # A resumed simultaneous ordering rebuilds the same subjects at a new
        # revision. Bind the offer to each original incarnation instead.
        incarnation = sha256(subject.logical_object_id.encode("utf-8")).hexdigest()[:24]
        return f"zone.change:entry-counter-transfer:{incarnation}:{subject.object_ref}"
    return f"zone.change:{snapshot.revision}:{snapshot.event_sequence + 1}:{subject.object_ref}"


def zone_change_snapshot_event(
    snapshot: ZoneChangeReplacementSnapshot,
    subject: ZoneChangeSubjectSnapshot,
) -> ReplaceableEvent:
    from ..replacement.entry_counter_types import BATTLEFIELD_ENTRY_COUNTER_SCOPE
    from ..rules.linked_graveyard_play import PRIOR_CONTROL_FIELD, REPLACEMENT_HANDLER

    has_linked_graveyard_exile = any(
        effect.effect_id.startswith(REPLACEMENT_HANDLER + ":") for effect in snapshot.effects
    )
    return ReplaceableEvent(
        event_id=_zone_change_snapshot_event_id(snapshot, subject),
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
            **({'composition_card_components':[row.to_dict() for row in subject.card_components]} if subject.card_components else {}),
            **({'commander_components':[row.to_dict() for row in subject.commander_components],
                'commander_component_returns':[],'commander_destination_epoch':0} if subject.commander_components else {}),
            "origin": subject.origin,
            "destination": subject.destination,
            "destination_controller": subject.destination_controller,
            **({PRIOR_CONTROL_FIELD: subject.controller} if has_linked_graveyard_exile else {}),
            "object_kind": "card" if subject.is_card_object else "noncard",
            "object_ref": subject.object_ref,
            "object_types": list(subject.object_types),
            **({BATTLEFIELD_ENTRY_COUNTER_SCOPE: True}
               if any(counter.additional for counter in subject.effect_entry_counters) else {}),
            **({ENTRY_TIME_COUNTER_TYPES_FIELD: subject.entry_time_counter_types.to_dict()}
               if subject.entry_time_counter_types is not None else {}),
            "logical_object_id": subject.logical_object_id,
            **({"prospective_subject": True} if subject.prospective_subject else {}),
            "transition_kind": subject.transition_kind.value,
            "owner": subject.owner,
            **({'_counter_permissions': subject.counter_permissions.to_dict()}
               if subject.counter_permissions.rules else {}),
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


def snapshot_entry_scope(snapshot: ZoneChangeReplacementSnapshot) -> EntryReplacementScope:
    """Seal the same simultaneous entry set for all private and public selections."""
    entering = tuple(subject for subject in snapshot.subjects if subject.destination == "battlefield")
    scope = EntryReplacementScope(tuple(subject.object_id for subject in entering),
        tuple(subject.object_id for subject in entering if subject.origin == "library"))
    leaving = tuple(subject.object_id for subject in snapshot.subjects if subject.destination != "battlefield")
    return scope.reserve_zone_changes(leaving) if leaving else scope


def zone_change_snapshot_events(snapshot: ZoneChangeReplacementSnapshot) -> tuple[ReplaceableEvent, ...]:
    from dataclasses import replace
    from ..replacement.amplify import AmplifyEntryOperation
    from .entry_cost_preparation import has_entry_land_discard

    events = tuple(zone_change_snapshot_event(snapshot, subject) for subject in snapshot.subjects)
    if not has_entry_land_discard(snapshot) and not any(isinstance(operation, AmplifyEntryOperation)
        for effect in snapshot.effects for operation in effect.operations):
        return events
    scope = snapshot_entry_scope(snapshot)
    return tuple(replace(event, entry_scope=scope) for event in events)


def source_zone_replacement_context(host, source, descriptor):
    from ..continuous_effect_model import ContinuousObjectIdentity
    from ..qualified_entry_state_model import EntryStateSourceSnapshot, QUALIFIED_ENTRY_STATE_HANDLER
    from ..external_entry_counter_model import EXTERNAL_ENTRY_COUNTER_HANDLER_IDS
    from ..rules.external_entry_counters import snapshot_external_entry_counter_source
    from ..rules.linked_graveyard_play import REPLACEMENT_HANDLER

    handler = descriptor.get("handler_id")
    if handler in EXTERNAL_ENTRY_COUNTER_HANDLER_IDS:
        return snapshot_external_entry_counter_source(host, source, descriptor)
    if handler == QUALIFIED_ENTRY_STATE_HANDLER:
        return EntryStateSourceSnapshot(source.ref, source.controller, source.tapped)
    if handler == REPLACEMENT_HANDLER:
        return ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    return None


def source_local_zone_replacement_effects(host, registry, record, card, subject, programs):
    """Collect only the current affected card's independently owned replacements."""
    from ..card_program_faces import program_matches_face
    from ..madness import MADNESS_REPLACEMENT_HANDLER_ID
    from ..opponent_discard_entry_model import OPPONENT_DISCARD_ENTRY_HANDLER
    from .self_entry_counters import program_static_component_is_applicable
    from .self_graveyard_exile import SELF_GRAVEYARD_REPLACEMENT_HANDLERS

    accepted = {*SELF_GRAVEYARD_REPLACEMENT_HANDLERS, MADNESS_REPLACEMENT_HANDLER_ID, OPPONENT_DISCARD_ENTRY_HANDLER}
    effects = []
    for program in programs:
        if (not program_matches_face(record, program, card)
                or not program_static_component_is_applicable(host, program, card)):
            continue
        for index, descriptor in enumerate(program.handlers):
            if descriptor.get("handler_id") in accepted:
                effects.extend(registry.subject_replacement_effects(
                    descriptor, subject=subject, component_id=f"{program.key}:{index}",
                ))
    return tuple(effects)


def intrinsic_subject_replacement_effects(
    handler, descriptor, *, subject, component_id, host, simultaneous_departures, fallback,
) -> tuple[ReplacementEffect, ...]:
    """Bind an intrinsic replacement against its immutable subject information."""
    from .chosen_basic_land_entry import ChosenBasicLandEntryHandler
    from .day_night_entry import DayNightEntryHandler
    from .linked_graveyard_entry import LinkedGraveyardEntryHandler
    from .entry_state import EntryRadChoiceHandler
    from .self_entry_counters import EntryCounterTransferHandler

    if isinstance(handler, (CountedCreatureTypeEntryDesignationHandler, EntryOpponentDesignationHandler,
                            LinkedGraveyardEntryHandler, EntryCounterTransferHandler, DayNightEntryHandler,
                            ChosenBasicLandEntryHandler, EntryRadChoiceHandler)):
        return handler.subject_replacement_effects(
            descriptor, subject=subject, component_id=component_id, host=host,
            simultaneous_departures=simultaneous_departures,
        )
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
    effect = fallback(
        descriptor,
        subject=subject,
        component_id=component_id,
    )
    return () if effect is None else (effect,)

def fixed_entry_condition_metrics(
    host: EntryConditionMetricHost,
    *,
    record: Any,
    card: Any,
    programs: Sequence[Any],
    destination_controller: str | None,
    prospective_name: str,
    cache: dict[str | None, Mapping[str, int]],
) -> Mapping[str, int]:
    conditions = tuple(
        descriptor.get("condition", {})
        for program in programs
        if program_matches_face(
            record,
            program,
            card,
            prospective_name=prospective_name or None,
        )
        for descriptor in program.handlers
        if descriptor.get("handler_id") == FIXED_ENTRY_CONDITION_HANDLER_ID
    )
    if not conditions:
        return {}
    if destination_controller not in cache:
        cache[destination_controller] = entry_condition_metrics(
            host,
            destination_controller,
        )
    metrics = cache[destination_controller]
    turn_metric = FixedEntryMetric.CONTROLLER_ACTIVE_TURN_NUMBER.value
    if any(isinstance(condition, Mapping) and condition.get("metric") == turn_metric for condition in conditions):
        return {**metrics, turn_metric: controller_active_turn_number(host, destination_controller)}
    return metrics


__all__ = [
    "active_zone_replacement_sources",
    "prospective_destination_controller",
    "validated_zone_change_snapshot_inputs",
    "zone_change_snapshot_event",
    "zone_change_subject_card",
]


def intrinsic_saga_aware_entry_preview(
    host, card, record, destination, controller, characteristics, card_types, subtypes, programs_for_record,
):
    from ..ability_fragment_primitives import current_saga_read_ahead_grants
    from ..entry_counters import intrinsic_entry_counters

    if destination != "battlefield" and "saga" in subtypes and current_saga_read_ahead_grants(host.state):
        return (), ()
    name = str(characteristics.get("name") or "")
    try:
        chapters = granted_read_ahead_entry_chapters(host, record, card, characteristics, name)
    except ValueError as exc:
        raise SemanticNodeError(str(exc)) from exc
    read_ahead_supported = bool(chapters) or printed_read_ahead_entry_is_supported(
        host, record, card, prospective_name=name, destination_controller=controller,
        characteristics=characteristics, subtypes=subtypes, programs_for_record=programs_for_record,
    )
    counters = intrinsic_entry_counters(
        characteristics, card_types=tuple(sorted(card_types)), card_subtypes=tuple(sorted(subtypes)),
        keywords=tuple(characteristics.get("keywords") or ()), read_ahead_supported=read_ahead_supported,
    )
    return counters, chapters


def granted_read_ahead_entry_chapters(host, record, card, characteristics, prospective_name):
    from ..read_ahead import GRANTED_READ_AHEAD_ENTRY, read_ahead_entry_handler_descriptor

    if characteristics.get(GRANTED_READ_AHEAD_ENTRY) is not True:
        return ()
    from ..card_program_faces import program_matches_face

    if record is None or not card.is_card_object or card.annotations.get("copy_overrides"):
        raise ValueError("Granted Read Ahead requires a represented physical Saga")
    chapters = set()
    for program in host.semantics.programs_for_oracle(record.oracle_id):
        event = str(program.event or "")
        if (not event.startswith("saga.chapter.") or not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, card, prospective_name=prospective_name or None)):
            continue
        number = event.removeprefix("saga.chapter.")
        if not number.isascii() or not number.isdecimal() or int(number) < 1:
            raise ValueError("Granted Read Ahead requires trusted numbered chapter programs")
        chapters.add(int(number))
    numbers = tuple(sorted(chapters))
    read_ahead_entry_handler_descriptor(numbers)
    return numbers


def printed_read_ahead_entry_is_supported(
    host: Any,
    record: Any | None,
    card: Any,
    *,
    prospective_name: str,
    destination_controller: str | None,
    characteristics: Mapping[str, Any],
    subtypes: set[str],
    programs_for_record,
) -> bool:
    from ..card_program_faces import program_matches_face, prospective_face_view
    from ..read_ahead import READ_AHEAD_ENTRY_HANDLER_ID
    from .entry_choices import ReadAheadEntryChoiceHandler
    from .self_entry_counters import program_static_component_is_applicable

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
    for program in programs_for_record(
        host,
        record,
        active_zone="all",
    ):
        if not program_matches_face(
            record,
            program,
            card,
            prospective_name=prospective_name or None,
        ) or not program_static_component_is_applicable(
            host,
            program,
            prospective_face_view(record, card, prospective_name),
            prospective_zone="battlefield",
            prospective_controller=destination_controller,
        ):
            continue
        for descriptor in program.handlers:
            if descriptor.get("handler_id") == READ_AHEAD_ENTRY_HANDLER_ID:
                nodes.append(handler.validate(descriptor))
    if len(nodes) != 1:
        raise SemanticNodeError(
            "Read Ahead entry requires one trusted typed handler"
        )

    import re

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
