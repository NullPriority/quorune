"""Prepare the selected discard before committing its dependent entry."""

from dataclasses import replace

from ..replacement import ReplacementChoiceRequired
from ..replacement_effects import SetField
from ..rules.entry_land_discard import FIELD, paid_selection, require_prepared_discard
from ..zone_trigger_events import ZoneTransitionKind


def has_entry_land_discard(snapshot):
    return any(isinstance(op, SetField) and op.field == FIELD
               for effect in snapshot.effects for op in effect.operations)


def _ordered_prefix(snapshot, *, selections, host, error_type, prospective_objects):
    from .zone_replacements import prepare_zone_change_replacement_snapshot

    # Each prefix reuses the ordinary ordering and requires exact selections.
    for count in range(len(selections) + 1):
        try:
            prepared = prepare_zone_change_replacement_snapshot(snapshot, selections=selections[:count],
                host=host, error_type=error_type, prospective_objects=prospective_objects,
                prepare_entry_discards=False)
        except ReplacementChoiceRequired:
            if count == len(selections):
                raise
        else:
            return prepared, count
    raise AssertionError("Expected a complete entry replacement prefix")


def prepare_entry_land_discard_snapshot(host, snapshot, *, selections, error_type, prospective_objects):
    from .zone_replacements import capture_zone_change_replacement_snapshot, prepare_zone_change_replacement_snapshot

    prepared, consumed = _ordered_prefix(snapshot, selections=selections, host=host, error_type=error_type,
                                         prospective_objects=prospective_objects)
    paid = {oid: choice for oid, parent in prepared.items() if (choice := paid_selection(parent.event)) is not None}
    if not paid:
        if consumed != len(selections):
            raise error_type("Entry discard selections were supplied without a paid discard")
        return prepared
    reserved = set(prepared)
    discards = {}
    for entrant, choice in paid.items():
        try:
            card = choice.current_card(host)
        except ValueError as exc:
            raise error_type(str(exc)) from exc
        if card.object_id in reserved:
            raise error_type("A land cannot pay two simultaneous entry discards")
        reserved.add(card.object_id)
        discards[entrant] = card.object_id
    changes = tuple((oid, "graveyard") for oid in discards.values())
    discard_snapshot = capture_zone_change_replacement_snapshot(host, changes,
        transition_kinds={oid: ZoneTransitionKind.DISCARD for oid in discards.values()}, error_type=error_type)
    children = prepare_zone_change_replacement_snapshot(discard_snapshot, selections=selections[consumed:],
                                                        host=host, error_type=error_type)
    return {oid: replace(parent, entry_land_discard=children[discards[oid]]) if oid in discards else parent
            for oid, parent in prepared.items()}


def expand_entry_land_discards(host, changes, prepared, kinds, *, error_type):
    parents = tuple(prepared[oid] for oid, _destination in changes)
    if not any(paid_selection(parent.event) is not None for parent in parents):
        return tuple(changes), prepared, kinds
    plans, transitions = dict(prepared), dict(kinds)
    prefix = []
    for parent in parents:
        if paid_selection(parent.event) is None:
            continue
        child = parent.entry_land_discard
        if child is None or child.object_id in plans:
            raise error_type("The entry land discard must be prepared once before entering")
        try:
            require_prepared_discard(parent, child).current_card(host)
        except ValueError as exc:
            raise error_type(str(exc)) from exc
        prefix.append((child.object_id, child.requested_destination))
        plans[child.object_id] = child
        transitions[child.object_id] = ZoneTransitionKind.DISCARD
    return (*prefix, *changes), plans, transitions


def prepare_special_entry_costs(snapshot, *, host, selections, error_type, prospective_objects,
                                event_ids, prepare_entry_discards):
    from ..replacement.operations import DevourEntryOperation
    from .graveyard_copy_preparation import has_graveyard_copy_operation, prepare_graveyard_copy_snapshot

    if prepare_entry_discards and has_entry_land_discard(snapshot):
        if host is None or event_ids is not None:
            raise error_type("Entry land discard requires its captured zone host")
        return prepare_entry_land_discard_snapshot(host, snapshot, selections=selections, error_type=error_type,
                                                   prospective_objects=prospective_objects)
    if has_graveyard_copy_operation(snapshot):
        if host is None or event_ids is not None:
            raise error_type("Graveyard copy requires its captured entry host")
        return prepare_graveyard_copy_snapshot(host, snapshot, selections=selections, error_type=error_type,
                                              prospective_objects=prospective_objects)
    if any(isinstance(op, DevourEntryOperation) for effect in snapshot.effects for op in effect.operations):
        if event_ids is not None:
            raise error_type("Explicit zone event identities do not represent Devour entry")
        from .devour_preparation import prepare_devour_snapshot

        return prepare_devour_snapshot(snapshot, selections=selections, error_type=error_type)
    return None
