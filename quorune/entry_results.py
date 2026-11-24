from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .rules.amplify import amplify_hand_reveals
from .counter_placement import commit_counter_events_from_resolution
from .entry_designations import EntryDesignationKind, validate_bound_entry_card_name, validate_designation
from .semantic_runtime.intents import SetCardDesignationIntent
from .entry_keyword_grants import (
    commit_entry_keyword_grants,
    EntryKeywordGrant,
    EntryKeywordGrantError,
)


class PreparedEntryResult(Protocol):
    event: Any
    effects: Sequence[Any]
    journal: Sequence[Any]
    keyword_grants: Sequence[EntryKeywordGrant]


def commit_entry_designations(
    host: Any,
    card: Any,
    *,
    color: str | None,
    creature_type: str | None,
    card_name: str | None = None,
) -> None:
    """Retain entry choices through the canonical card-designation intent owner."""
    choices = (
        (EntryDesignationKind.COLOR, color),
        (EntryDesignationKind.CREATURE_TYPE, creature_type),
    )
    for kind, value in choices:
        if value is not None:
            host.set_card_designation_intent(
                SetCardDesignationIntent(
                    object_ref=card.ref,
                    designation=kind.annotation,
                    value=validate_designation(kind, value),
                    actor=card.controller,
                    reason="intrinsic battlefield-entry choice",
                )
            )
    if card_name is not None:
        host.set_card_designation_intent(SetCardDesignationIntent(
            object_ref=card.ref, designation="chosen_name", value=validate_bound_entry_card_name(card_name),
            actor=card.controller, reason="intrinsic battlefield-entry choice",
        ))


def commit_prepared_copy_values(prepared,card,*,error_type,preserve_physical_face=False):
    """Commit one validated copied-value result before entry observers."""
    from .replacement.face_status_copy import ENTRY_COPY_HISTORY_FIELD, ScopedCopyValues

    copy_values = prepared.event.payload.get('entry_copy')
    retained = prepared.event.payload.get(ENTRY_COPY_HISTORY_FIELD)
    if retained is not None:
        try:
            history = ScopedCopyValues.from_dict(retained)
            if not history.ordered or history.effective.values != copy_values:
                raise ValueError('The prepared copy and copy history disagree')
        except ValueError as exc:
            raise error_type(str(exc)) from exc
        install_copy_history(card, history.timestamp_effective(card.zone_timestamp))
    elif copy_values is not None:
        install_copiable_values(card, copy_values, error_type=error_type, preserve_physical_face=preserve_physical_face)


def install_copiable_values(card, values, *, error_type, preserve_physical_face):
    """Install validated copiable characteristics on the same physical object."""
    from .entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD
    from .defined_token_faces import defined_token_definition
    from .replacement.immutable import thaw_value
    from .shared_type_doors import SharedTypeDoors, copied_room_door_ids

    if not isinstance(values, Mapping) or COPIABLE_ORACLE_FIELD not in values:
        raise error_type('Prepared copy values are malformed')
    card.annotations['copy_overrides'] = thaw_value(values)
    copied_doors = copied_room_door_ids(values)
    if copied_doors is not None:
        printed = card.shared_type_doors is not None and card.shared_type_doors.printed
        card.shared_type_doors = SharedTypeDoors(copied_doors, printed=printed)
    if (defined_token_definition(card) is None
            and not (preserve_physical_face and card.object_kind == "card" and not card.is_token)):
        card.active_face=card.annotations['copy_overrides'].get(COPIABLE_FACE_FIELD)


_TRACK_LINKED_COPY_FORMS = "retain_linked_copy_forms"


def retain_linked_copy_forms(card):
    """Preserve the ability origin of this source's future copy forms."""
    from .replacement.face_status_copy import current_copy_history

    if card.annotations.get(_TRACK_LINKED_COPY_FORMS) is not True:
        history = current_copy_history(card)
        card.annotations[_TRACK_LINKED_COPY_FORMS] = True
        install_copy_history(card, history)


def install_copy_history(card, history):
    from .replacement.face_status_copy import COPY_HISTORY_FIELD
    from .replacement.immutable import thaw_value

    form = history.effective
    if form.values is None:
        card.annotations.pop("copy_overrides", None)
    else:
        card.annotations["copy_overrides"] = thaw_value(form.values)
    card.shared_type_doors = form.doors
    if history.ordered or card.annotations.get(_TRACK_LINKED_COPY_FORMS) is True:
        card.annotations[COPY_HISTORY_FIELD] = history.to_dict()
    else:
        card.annotations.pop(COPY_HISTORY_FIELD, None)
    legacy = card.annotations.get("until_end_of_turn")
    if legacy is not None:
        legacy.pop("copy_overrides_previous", None)


def begin_face_status_copy(card, values):
    from .replacement.face_status_copy import CopyExpiry, current_copy_history, scoped_copy_form

    history = current_copy_history(card)
    install_copy_history(card, history.apply(CopyExpiry.FACE_DOWN, scoped_copy_form(values,timestamp=card.zone_timestamp)))


def record_temporary_copy(card, values, *, timestamp=0):
    from copy import deepcopy
    from .replacement.face_status_copy import COPY_HISTORY_FIELD, CopyExpiry, current_copy_history, scoped_copy_form

    if COPY_HISTORY_FIELD in card.annotations:
        history = current_copy_history(card)
        install_copy_history(card, history.apply(CopyExpiry.END_OF_TURN,scoped_copy_form(values,timestamp=timestamp)))
    else:
        legacy = card.annotations.setdefault("until_end_of_turn", {})
        if "copy_overrides_previous" not in legacy:
            legacy["copy_overrides_previous"] = deepcopy(card.annotations.get("copy_overrides"))
        card.annotations["copy_overrides"] = values


def record_persistent_copy(card, values, *, host):
    """Install the new copy for the rest of the current incarnation."""
    from .replacement.face_status_copy import ScopedCopyValues, scoped_copy_form

    from .abilities import ActivatedAbility
    from .activation_usage import reset_copied_activation_usage
    from .linked_ability_exile_state import expire_linked_graveyard_entry_source

    from .replacement.immutable import thaw_value

    copied_abilities = tuple(
        ActivatedAbility.from_dict(thaw_value(raw)) for raw in values.get("activated_abilities", ())
    )
    reset_copied_activation_usage(card, ability_ids=(
        ability.ability_id for ability in copied_abilities if ability.activation_limit is not None
    ))
    expire_linked_graveyard_entry_source(card)
    timestamp = host._next_zone_timestamp() if card.annotations.get(_TRACK_LINKED_COPY_FORMS) is True else 0
    install_copy_history(card, ScopedCopyValues(scoped_copy_form(values, timestamp=timestamp)))


def expire_copy_scope(card, scope):
    from copy import deepcopy
    from .replacement.face_status_copy import COPY_HISTORY_FIELD, CopyExpiry, current_copy_history

    from .linked_ability_exile_state import expire_linked_graveyard_entry_source

    expire_linked_graveyard_entry_source(card, scope)
    if COPY_HISTORY_FIELD in card.annotations:
        history = current_copy_history(card)
        if any(known == scope for known, _ in history.ordered):
            install_copy_history(card, history.expire(scope))
    elif scope == CopyExpiry.END_OF_TURN:
        legacy = card.annotations.get("until_end_of_turn") or {}
        if "copy_overrides_previous" in legacy:
            previous = legacy["copy_overrides_previous"]
            if previous is None:
                card.annotations.pop("copy_overrides", None)
            else:
                card.annotations["copy_overrides"] = deepcopy(previous)


def reset_scoped_copy_on_departure(card):
    from .replacement.face_status_copy import COPY_HISTORY_FIELD, ScopedCopyValues

    card.annotations.pop(_TRACK_LINKED_COPY_FORMS, None)
    raw = card.annotations.get(COPY_HISTORY_FIELD)
    if raw is not None:
        history = ScopedCopyValues.from_dict(raw)
        install_copy_history(card, ScopedCopyValues(history.baseline))


def _commit_linked_entry_opponent(prepared, card, *, host) -> None:
    from .entry_designations import (
        BoundEntryOpponentChoice, COUNTED_ENTRY_OPPONENT_TEMPLATE, EntryOpponentChoice,
        ENTRY_OPPONENT_FIELD, LINKED_ENTRY_OPPONENT_FIELD, LINKED_ENTRY_PLAYER_TEMPLATE,
    )
    from .rules.linked_entry_opponent import entry_opponent_copy_form

    raw = prepared.event.payload.get(ENTRY_OPPONENT_FIELD)
    if raw is None:
        return
    if card.zone != "battlefield":
        raise ValueError("An entry opponent choice requires its completed battlefield entry")
    choice = EntryOpponentChoice.from_dict(raw)
    provider = host.semantics.get(choice.entry_program_key) if host is not None else None
    if choice.any_player:
        if (
            host is None or choice.seat not in host.active_seats or provider is None
            or provider.provenance.get("template_id") != LINKED_ENTRY_PLAYER_TEMPLATE
            or not host.semantic_program_is_current_trusted(provider)
        ):
            raise ValueError("The selected player requires its original admitted entry component")
    else:
        if provider is not None and provider.provenance.get("template_id") == COUNTED_ENTRY_OPPONENT_TEMPLATE:
            return
        if choice.seat == card.controller:
            raise ValueError("The initial entry choice requires an opponent")
    retain_linked_copy_forms(card)
    card.annotations[LINKED_ENTRY_OPPONENT_FIELD] = BoundEntryOpponentChoice(
        choice, card.logical_object_id, card.active_face, entry_opponent_copy_form(card),
    ).to_dict()


def _commit_linked_entry_target_color(host, prepared, card):
    from .rules.chosen_color_target_shapes import (
        LINKED_ENTRY_COLOR_TARGET_FIELD, color_target_entry_binding, linked_color_target_entry_programs,
    )

    color = prepared.event.payload.get("entry_chosen_color")
    if host is None or color is None or card.zone != "battlefield":
        return
    keys = linked_color_target_entry_programs(host, card)
    if keys:
        retain_linked_copy_forms(card)
        card.annotations[LINKED_ENTRY_COLOR_TARGET_FIELD] = color_target_entry_binding(card, color, keys)


def commit_prepared_entry_identity_results(prepared,card,*,error_type,preserve_physical_face=False,host=None):
    """Commit copied values and incarnation-bound choices before entry observers."""
    commit_prepared_copy_values(prepared,card,error_type=error_type,preserve_physical_face=preserve_physical_face)
    from .linked_ability_exile_state import commit_linked_graveyard_entry
    from .rules.linked_graveyard_entry import LINKED_GRAVEYARD_RESULTS_FIELD

    if LINKED_GRAVEYARD_RESULTS_FIELD in prepared.event.payload:
        if host is None:
            raise error_type("Linked graveyard entry requires its zone owner")
        commit_linked_graveyard_entry(host, prepared, card)
    from .tribute_entry_results import commit_tribute_entry_outcomes
    from .zone_devour import commit_devour_entry_receipt
    try:
        _commit_linked_entry_opponent(prepared, card, host=host)
        _commit_linked_entry_target_color(host, prepared, card)
        commit_tribute_entry_outcomes(prepared,card)
        if host is not None:
            commit_devour_entry_receipt(host, prepared, card)
    except (ValueError,TypeError) as exc:raise error_type(str(exc)) from exc


def commit_prepared_entry_results(
    host: Any,
    prepared: PreparedEntryResult,
    card: Any,
    *,
    reason: str,
    log: bool,
    error_type: type[Exception],
) -> None:
    """Commit typed nested-counter and layer-6 results after one entry."""

    from .entry_reveal import ENTRY_REVEAL_FIELD
    from .effect_runtime.hand_inspection import commit_entry_hand_reveal, validate_entry_hand_reveal
    payload = prepared.event.payload
    amplify_selections = amplify_hand_reveals(payload)
    for selection in amplify_selections:
        validate_entry_hand_reveal(host, selection)
    for selection in (payload.get(ENTRY_REVEAL_FIELD), *amplify_selections):
        commit_entry_hand_reveal(host, selection, source_ref=card.ref)

    from .token_copy_value_model import token_copy_value_mode
    commit_prepared_entry_identity_results(prepared,card,error_type=error_type,
        preserve_physical_face=token_copy_value_mode(host)==1,host=host)
    from .rules.entry_subtype_grants import entry_subtype_grants,commit_entry_subtype_grants
    try:commit_entry_subtype_grants(host,card,entry_subtype_grants(prepared.event))
    except (ValueError,TypeError) as exc:raise error_type(str(exc)) from exc

    commit_counter_events_from_resolution(
        host,
        prepared,
        reason=reason,
        log=log,
        error_type=error_type,
        dispatch_events=False,
    )
    from .preparation_model import PREPARE_ENTRY_FIELD,PrepareSpellSpec
    if card.zone=='battlefield' and PREPARE_ENTRY_FIELD in prepared.event.payload:
        from .preparation import become_prepared
        become_prepared(host,card,spell=PrepareSpellSpec.from_dict(prepared.event.payload[PREPARE_ENTRY_FIELD]))
    try:
        commit_entry_keyword_grants(host, card, prepared.keyword_grants)
    except EntryKeywordGrantError as exc:
        raise error_type(str(exc)) from exc


def validate_pending_amplify_reveals(host, batch, effects, selection, *, error_type):
    from .effect_runtime.hand_inspection import validate_entry_hand_reveal
    from .replacement.amplify import AmplifyEntryOperation
    from .semantic_runtime.amplify import hand_creature_types

    for event in batch.events:
        for row in amplify_hand_reveals(event.payload):
            validate_entry_hand_reveal(host, row)
    if not isinstance(selection, Mapping) or not selection.get("objects"):
        return
    effect = next((effect for effect in effects if effect.effect_id == selection["effect_id"]), None)
    if effect is None:
        return
    for operation in effect.operations:
        if not isinstance(operation, AmplifyEntryOperation):
            continue
        captured = {row.ref: row for row in operation.candidates}
        for ref in selection["objects"]:
            row = captured.get(ref)
            if row is None:
                raise error_type("Amplify selected an uncaptured hand card")
            card = validate_entry_hand_reveal(host, row.reveal())
            if hand_creature_types(host, card) != row.creature_types:
                raise error_type("Amplify selected card's creature types changed during the choice")


__all__ = ["PreparedEntryResult", "commit_prepared_entry_results", "validate_pending_amplify_reveals"]
