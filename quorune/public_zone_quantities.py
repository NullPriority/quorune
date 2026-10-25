from __future__ import annotations

"""Read-only card cardinalities and public owned-zone collection evaluation."""

from .public_zone_quantity_model import validate_public_zone_quantity


def current_public_zone_quantity(host,source,quantity,*,enforce_applicability=True):
    from .characteristic_fragments import CharacteristicQuantityScope as S,CharacteristicQuantityReduction as R
    validate_public_zone_quantity(quantity)
    controller=source.controller
    if controller not in host.state.players:raise ValueError('Zone quantity requires a represented controller')
    seats=tuple(seat for seat,player in host.state.players.items() if player.in_game
        and (quantity.scope is S.ALL_ZONES or (seat==controller if quantity.scope is S.CONTROLLER_ZONE else seat!=controller)))
    hidden=quantity.query.zones in {('hand',),('library',)}
    filtered=bool(quantity.query.types_all or quantity.query.types_any or quantity.query.excluded_types)
    counts=[];matched=[];seen=set()
    for seat in seats:
        count=0
        for zone in quantity.query.zones:
            for object_id in host.state.players[seat].zones[zone]:
                if object_id in seen:continue
                seen.add(object_id);card=host.state.cards.get(object_id)
                if card is None:raise ValueError('A zone cardinality member is unavailable')
                if card.object_kind!='card':continue
                if hidden or not filtered and quantity.reduction is R.OBJECT_COUNT:
                    count+=1;continue
                if card.face_down:continue
                from .continuous_effects import Layer
                from .object_query import object_query_result,object_matches_query
                effective=host._effective_card_data(card,maximum_layer=Layer.COLOR,
                    _enforce_static_component_applicability=enforce_applicability)
                row=object_query_result(card,effective,type_parts=host._type_parts(str(effective.get('type_line') or '')),
                    known_to_actor=True,attached_to_ref=None)
                if object_matches_query(row,quantity.query):matched.append((row,effective,'card'))
        counts.append(count)
    if hidden:
        result=max(counts,default=0) if quantity.reduction is R.MAXIMUM_ZONE_SIZE else sum(counts)
    elif not filtered and quantity.reduction is R.OBJECT_COUNT:result=sum(counts)
    else:
        from .public_quantity_reductions import reduce_public_quantity
        result=reduce_public_quantity(matched,quantity.reduction)
    return result*quantity.multiplier+quantity.offset
