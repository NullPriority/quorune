from __future__ import annotations

"""Apply layer-7 scalar operations to a detached characteristic proposal."""

from .continuous_effect_model import ContinuousEffectError
from .recipient_modifier_model import (RecipientQuantityKind,validate_recipient_modifier,
    RECIPIENT_MODIFIER_OPERATION)
from .counter_snapshot import permanent_counter_snapshot
from .creature_subtypes import canonical_creature_subtype
from .mana_symbol_counts import colored_mana_symbol_count


def recipient_quantity_amount(spec,state,context):
    if spec.kind is RecipientQuantityKind.COLORS:
        if not set(state.colors)<={'W','U','B','R','G'}:raise ContinuousEffectError('Recipient colors are malformed')
        amount=len(set(state.colors))
    elif spec.kind is RecipientQuantityKind.COUNTERS:
        if 'counters' not in context:raise ContinuousEffectError('Recipient counters are unavailable')
        amount=permanent_counter_snapshot(context['counters']).get(spec.counter,0)
    elif spec.kind is RecipientQuantityKind.MANA_SYMBOLS:
        amount=colored_mana_symbol_count(state.mana_cost,(spec.mana_color,))
    elif spec.kind is RecipientQuantityKind.CREATURE_TYPES:
        amount=len({kind for value in state.subtypes if (kind:=canonical_creature_subtype(value)) is not None})
    else:
        raise ContinuousEffectError('A shared-type quantity requires its typed battlefield snapshot')
    return min(amount,spec.maximum) if spec.maximum is not None else amount


def apply_stat_operation(state,operation,context):
    """Return false for operations belonging to another characteristic layer."""
    op=operation.op;value=operation.value
    if op=='set_power':state.power=int(value);return True
    if op=='set_toughness':state.toughness=int(value);return True
    if op=='set_power_toughness':state.power=int(value[0]);state.toughness=int(value[1]);return True
    if op=='switch_power_toughness':state.power,state.toughness=state.toughness,state.power;return True
    if op==RECIPIENT_MODIFIER_OPERATION:
        spec=validate_recipient_modifier(value)
        if spec.kind is RecipientQuantityKind.SHARED_CREATURE_TYPES:
            from .continuous_effect_model import ContinuousObjectIdentity
            from .shared_creature_type_model import shared_creature_type_count
            identity=ContinuousObjectIdentity(context.get('object_id'),context.get('logical_object_id'))
            amount=shared_creature_type_count(value['objects'],recipient=identity,creature_types=state.subtypes)
            if spec.maximum is not None:amount=min(amount,spec.maximum)
        else:amount=recipient_quantity_amount(spec,state,context)
        power,toughness=value['power']*amount,value['toughness']*amount
    elif op=='modify_power_toughness':power,toughness=int(value[0]),int(value[1])
    else:return False
    if state.power is not None:state.power+=power
    if state.toughness is not None:state.toughness+=toughness
    return True
