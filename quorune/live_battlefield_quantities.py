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
    count = 0
    for card in host.state.cards.values():
        if card.zone != 'battlefield' or card.phased_out:
            continue
        data = host._effective_card_data(card)
        row = object_query_result(card, data, type_parts=host._type_parts(str(data.get('type_line') or '')),
            known_to_actor=True, attached_to_ref=None)
        count += object_matches_query(row, query)
    return count
