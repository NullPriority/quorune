from __future__ import annotations

"""Pay separate library-placement units through the canonical zone owner."""

from dataclasses import replace

from ..errors import GameRuleError
from ..semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent


def cost_event_namespace(intent):
    from hashlib import sha256
    from ..util import stable_json
    # Owner permutations remain secret. Only public membership and pinned
    # resource incarnations participate in externally visible event identities.
    public = {'payer': intent.actor, 'unit_size': intent.unit_size,
        'units': [{'owner': order.chooser, 'refs': sorted(order.members)} for order in intent.orders],
        'incarnations': dict(intent.incarnations)}
    return sha256(stable_json(public).encode('utf-8')).hexdigest()


def _selected_cost_cards(host, intent):
    result = {}
    for order in intent.orders:
        host._require_seat(order.chooser, in_game=True)
        for ref in order.members:
            card = host._resolve_object(intent.actor, ref, zones={'graveyard'})
            if (card.owner != order.chooser or card.object_kind != 'card'
                    or card.logical_object_id != intent.incarnations[ref]):
                raise GameRuleError('A grouped graveyard payment lost its owner or selected incarnation')
            result[ref] = card.object_id
    return result


def commit_graveyard_library_cost(host, intent):
    from ..semantic_runtime.zone_replacements import (
        capture_zone_change_replacement_snapshot,
        prepare_zone_change_replacement_snapshot,
    )
    from ..zone_transitions import ZoneTransitionOwner
    if not isinstance(intent, GraveyardLibraryCostBatchIntent):
        raise GameRuleError('Grouped graveyard cost requires its typed batch')
    host._require_seat(intent.actor, in_game=True)
    # A later replacement pause rolls back all preceding units. Saved choices
    # replay those units against the same starting state before continuing.
    with host.transaction():
        selected = _selected_cost_cards(host, intent)
        used = set()
        owner = ZoneTransitionOwner(host); namespace = cost_event_namespace(intent)
        for index, order in enumerate(intent.orders):
            public_refs = tuple(sorted(order.members))
            changes = tuple((selected[ref], 'library') for ref in public_refs)
            snapshot = capture_zone_change_replacement_snapshot(host, changes, error_type=GameRuleError)
            event_ids = {ref: f'graveyard.cost:{namespace}:{index}:{ref}' for ref in public_refs}
            events = set(event_ids.values())
            selections = tuple(value for value in intent.replacement_selections if value['event_id'] in events)
            used.update(index for index, value in enumerate(intent.replacement_selections)
                        if value['event_id'] in events)
            prepared = prepare_zone_change_replacement_snapshot(snapshot, selections=selections,
                event_ids=event_ids, error_type=GameRuleError)
            owner.move_cards_simultaneously(changes, reason=intent.reason, log=False,
                prepared_replacements=prepared,
                library_order=replace(order, members=tuple(selected[ref] for ref in order.members)))
        if used != set(range(len(intent.replacement_selections))):
            raise GameRuleError('Grouped graveyard cost contains stale replacement selections')
    return tuple(ref for order in intent.orders for ref in sorted(order.members))
