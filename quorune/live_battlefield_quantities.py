from __future__ import annotations

"""Read current public battlefield membership for an applied instruction."""

from dataclasses import replace
from .live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from .object_query import object_matches_query, object_query_result


def current_live_battlefield_count(host, controller: str, spec: LiveBattlefieldQuantitySpec,
                                 *, source_ref: str | None = None) -> int:
    if controller not in host.state.players:
        raise ValueError('Live battlefield count controller is unavailable')
    if spec.exclude_source and (type(source_ref) is not str or not source_ref):
        raise ValueError('Source-excluding live count requires its physical source reference')
    query = replace(spec.query,
        controller=controller if spec.controller_relation == 'source_controller' else None,
        excluded_controllers=(controller,) if spec.controller_relation == 'source_opponents' else (),
        exclude_ref=source_ref if spec.exclude_source else None)
    numeric = query.numeric_characteristic
    membership = replace(query, numeric_characteristic=None) if numeric is not None else query
    count = 0
    for card in host.state.cards.values():
        if card.zone != 'battlefield' or card.phased_out:
            continue
        if spec.schema_version == 3 and card.attacking != controller:
            continue
        if spec.schema_version == 2:
            if card.controller != controller:
                continue
            row = host._public_object_query_result(card, _maximum_layer=None)
            if numeric is not None:
                statistic = row.effective_power if numeric.characteristic.value == 'power' else row.effective_toughness
                if statistic is None and object_matches_query(row, membership):
                    raise ValueError('A counted creature has unavailable effective characteristics')
        else:
            data = host._effective_card_data(card)
            row = object_query_result(card, data, type_parts=host._type_parts(str(data.get('type_line') or '')),
                known_to_actor=True, attached_to_ref=None)
        count += object_matches_query(row, query)
    return count


def current_live_attacking_shared_type_count(host, controller: str, spec: LiveBattlefieldQuantitySpec,
                                            *, source_ref: str) -> int:
    """Count each other current attacking creature once when any type overlaps."""
    from .continuous_effect_model import ContinuousObjectIdentity
    from .object_query import query_objects
    from .shared_creature_type_model import (
        SharedCreatureTypeObject, canonical_creature_types, shared_creature_type_count,
    )

    rows = host.affected_permanent_object_rows(controller)
    subject = next((row for row in rows if row.ref == source_ref and row.zone == "battlefield"
                    and not row.phased_out), None)
    if subject is None:
        return 0
    recipient = ContinuousObjectIdentity(subject.object_id, subject.logical_object_id)
    members = tuple(
        SharedCreatureTypeObject(
            ContinuousObjectIdentity(row.object_id, row.logical_object_id),
            canonical_creature_types(row.subtypes),
        )
        for row in query_objects(rows, spec.query)
    )
    return shared_creature_type_count(members, recipient=recipient, creature_types=subject.subtypes)
