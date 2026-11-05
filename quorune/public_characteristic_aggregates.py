from __future__ import annotations

"""Current public characteristic aggregates for resolution and cost values."""

from dataclasses import replace
from .characteristic_query_boundary_model import PUBLIC_ZONE_FRONT_FACE_VERSION
from .continuous_effect_model import Layer
from .object_query import exact_numeric_characteristic,object_matches_query,object_query_result
from .public_characteristic_aggregate_model import PublicCharacteristicAggregateSpec,PUBLIC_STAT_AGGREGATE_CAPABILITY
from .public_quantity_reductions import public_mana_value


def current_public_characteristic_aggregate(host,controller:str,spec:PublicCharacteristicAggregateSpec):
    if controller not in host.state.players:
        raise ValueError('Public stat aggregate controller is unavailable')
    graveyard = spec.query.zones == ('graveyard',)
    if graveyard and getattr(host.state, 'characteristic_query_boundary_version', None) != PUBLIC_ZONE_FRONT_FACE_VERSION:
        raise ValueError('Distinct graveyard mana values require the current public face view')
    query=replace(spec.query,
        controller=controller if not graveyard and spec.controller_relation=='source_controller' else None,
        owner=controller if graveyard else None,
        excluded_controllers=(controller,) if spec.controller_relation=='source_opponents' else ())
    cards = (tuple(host.state.cards.get(oid) for oid in host.state.players[controller].zones['graveyard'])
             if graveyard else host.state.cards.values())
    values=[]
    for card in cards:
        if graveyard:
            if card is None or card.zone != 'graveyard' or card.owner != controller:
                raise ValueError('A public graveyard aggregate member is unavailable')
            if card.object_kind != 'card' or card.face_down:
                continue
        elif card.zone != 'battlefield' or card.phased_out:
            continue
        data = (host._effective_card_data(card, maximum_layer=Layer.COLOR)
                if spec.characteristic == 'mana_value' else host._effective_card_data(card))
        parts=host._type_parts(str(data.get('type_line') or ''))
        row=object_query_result(card,data,type_parts=parts,known_to_actor=True,attached_to_ref=None)
        if not object_matches_query(row,query):continue
        if spec.characteristic == 'mana_value':
            value = public_mana_value(data)
        else:
            value = exact_numeric_characteristic(card, data, spec.characteristic) if 'creature' in parts[0] else 0
        if type(value) is not int:
            raise ValueError('A matched current creature stat is unavailable')
        values.append(value)
    if spec.reduction == 'distinct_count':
        return len(set(values))
    return sum(values) if spec.reduction=='sum' else max(values,default=0)
