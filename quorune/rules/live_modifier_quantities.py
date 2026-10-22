from __future__ import annotations

"""Read closed current player or attached-object values for layer-safe modifiers."""

import math
from ..characteristic_fragments import CharacteristicQuantityScope,CharacteristicQuantityReduction
from ..continuous_effects import Layer


def current_modifier_quantity(host,source,quantity,*,enforce_applicability=True):
    controller=host.state.players.get(source.controller)
    if controller is None:
        raise ValueError('Current quantity source controller is unavailable')
    if quantity.scope is CharacteristicQuantityScope.CONTROLLER_LIFE_TOTAL:
        if type(controller.life) is not int:
            raise ValueError('Current life total is unavailable')
        return max(0,controller.life)
    if quantity.scope is CharacteristicQuantityScope.OPPONENT_POISON_COUNTERS:
        values=[]
        for seat,player in host.state.players.items():
            if type(player.in_game) is not bool:
                raise ValueError('Current opponent roster is unavailable')
            if seat!=source.controller and player.in_game:
                if type(player.poison) is not int or player.poison<0:
                    raise ValueError('Current opponent poison count is unavailable')
                values.append(player.poison)
        return sum(values)
    if quantity.scope is not CharacteristicQuantityScope.ATTACHED_OBJECT:
        raise ValueError('Current modifier quantity requires a represented reference')
    if source.attached_to is None:
        return 0
    from ..attachments import attached_object_identity
    identity=attached_object_identity(host.state.cards,source)
    if identity is None:
        raise ValueError('Current attached-object identity is unavailable')
    recipient=host.state.cards[identity.object_id]
    data=host._effective_card_data(recipient,maximum_layer=Layer.COLOR,
        _enforce_static_component_applicability=enforce_applicability)
    if quantity.reduction is CharacteristicQuantityReduction.DISTINCT_COLORS:
        colors=data.get('colors')
        if not isinstance(colors,(list,tuple)) or any(type(c) is not str or c not in {'W','U','B','R','G'} for c in colors):
            raise ValueError('Current attached-object colors are unavailable')
        return len(set(colors))
    value=data.get('mana_value')
    if type(value) not in {int,float} or not math.isfinite(value) or value<0 or int(value)!=value:
        raise ValueError('Current attached-object mana value is unavailable')
    return int(value)
