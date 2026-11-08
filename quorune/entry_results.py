from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .rules.amplify import amplify_hand_reveals
from .counter_placement import commit_counter_events_from_resolution
from .entry_designations import EntryDesignationKind, validate_designation
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


def commit_prepared_copy_values(prepared,card,*,error_type):
    """Commit one validated copied-value result before entry observers."""
    copy_values = prepared.event.payload.get('entry_copy')
    if copy_values is not None:
        from .entry_copy import COPIABLE_ORACLE_FIELD
        from .replacement.immutable import thaw_value
        if not isinstance(copy_values, Mapping) or COPIABLE_ORACLE_FIELD not in copy_values:
            raise error_type('Prepared entry copy values are malformed')
        card.annotations['copy_overrides'] = thaw_value(copy_values)
        from .shared_type_doors import SharedTypeDoors, copied_room_door_ids
        copied_doors = copied_room_door_ids(copy_values)
        if copied_doors is not None:
            printed = card.shared_type_doors is not None and card.shared_type_doors.printed
            card.shared_type_doors = SharedTypeDoors(copied_doors, printed=printed)

        from .entry_copy import COPIABLE_FACE_FIELD
        from .defined_token_faces import defined_token_definition
        if defined_token_definition(card) is None:
            card.active_face=card.annotations['copy_overrides'].get(COPIABLE_FACE_FIELD)


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

    commit_prepared_copy_values(prepared,card,error_type=error_type)
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
