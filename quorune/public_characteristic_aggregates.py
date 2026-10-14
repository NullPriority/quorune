from __future__ import annotations

"""Current public battlefield stat aggregates for resolution and cost values."""

from dataclasses import replace
from .object_query import exact_numeric_characteristic,object_matches_query,object_query_result
from .public_characteristic_aggregate_model import PublicCharacteristicAggregateSpec,PUBLIC_STAT_AGGREGATE_CAPABILITY


def current_public_characteristic_aggregate(host,controller:str,spec:PublicCharacteristicAggregateSpec):
    if controller not in host.state.players:
        raise ValueError('Public stat aggregate controller is unavailable')
    query=replace(spec.query,
        controller=controller if spec.controller_relation=='source_controller' else None,
        excluded_controllers=(controller,) if spec.controller_relation=='source_opponents' else ())
    values=[]
    for card in host.state.cards.values():
        if card.zone!='battlefield' or card.phased_out:continue
        data=host._effective_card_data(card)
        parts=host._type_parts(str(data.get('type_line') or ''))
        row=object_query_result(card,data,type_parts=parts,known_to_actor=True,attached_to_ref=None)
        if not object_matches_query(row,query):continue
        value=exact_numeric_characteristic(card,data,spec.characteristic) if 'creature' in parts[0] else 0
        if type(value) is not int:
            raise ValueError('A matched current creature stat is unavailable')
        values.append(value)
    return sum(values) if spec.reduction=='sum' else max(values,default=0)
