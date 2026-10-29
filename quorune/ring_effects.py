"""Ring emblem effects independent of abilities carried by its bearer."""
from __future__ import annotations

from typing import Iterable

from .continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity,
    ContinuousOperation, Layer,
)
from .ring_model import RingObjectIdentity, RingRuleView, RingStateError


def ring_legendary_effects(
    views: Iterable[RingRuleView], *, maximum_layer: Layer | None = None,
) -> tuple[ContinuousEffect, ...]:
    """Build current layer-four effects using the emblem's original timestamp."""
    if maximum_layer is not None and maximum_layer < Layer.TYPE:
        return ()
    effects = []
    for view in views:
        if not isinstance(view, RingRuleView):
            raise RingStateError('Ring effects require immutable current source facts')
        bearer = view.state.bearer
        if bearer is None:
            continue
        effects.append(ContinuousEffect(
            effect_id='ring:'+view.state.emblem.logical_object_id+':legendary',
            source_id=view.state.emblem.object_id,
            layer=Layer.TYPE, sublayer='4', timestamp=view.timestamp,
            operations=(ContinuousOperation('add_types',('Legendary',),field='supertypes'),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            relation=ContinuousEffectRelation.SOURCE_RING_BEARER_OBJECT,
            related_object=ContinuousObjectIdentity(
                bearer.identity.object_id,bearer.identity.logical_object_id)))
    return tuple(effects)


def ring_power_block_allowed(
    views: Iterable[RingRuleView], *, attacker: RingObjectIdentity,
    attacker_power: int | None, blocker_power: int | None,
) -> bool:
    """Apply only the external greater-power restriction, not other block rules."""
    if not isinstance(attacker,RingObjectIdentity):
        raise RingStateError('A Ring block comparison requires an exact attacker incarnation')
    restricted = False
    for view in views:
        if not isinstance(view,RingRuleView):
            raise RingStateError('Ring block restrictions require typed source facts')
        if view.state.bearer is not None and view.state.bearer.identity == attacker:
            restricted = True
    if not restricted:
        return True
    if type(attacker_power) is not int or type(blocker_power) is not int:
        raise RingStateError('Ring block comparison requires known current powers')
    return blocker_power <= attacker_power
