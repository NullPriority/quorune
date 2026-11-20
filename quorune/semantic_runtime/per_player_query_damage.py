"""Seal all recipient quantities before the existing damage batch can commit."""
from ..announced_damage import AllocatedDamageRecipient
from ..fixed_damage_set import snapshot_fixed_damage_set
from ..fixed_damage_set_model import FixedDamageSetSpec,PlayerDamageGroup
from ..per_player_query_damage_model import (
    PerPlayerQueryDamageSpec,BoundPerPlayerQueryDamage,BOUND_PER_PLAYER_QUERY_DAMAGE_KIND,
)
from .query_effect_amounts import resolve_public_query_amount


def resolve_per_player_query_damage(host,value,item):
    if value.get('kind')==BOUND_PER_PLAYER_QUERY_DAMAGE_KIND:
        bound=BoundPerPlayerQueryDamage.from_dict(value)
        if bound.controller!=item.controller:raise ValueError('Bound damage controller changed')
        return bound.to_dict()
    spec=PerPlayerQueryDamageSpec.from_dict(value)
    recipients=snapshot_fixed_damage_set(host,actor=item.controller,
        spec=FixedDamageSetSpec((PlayerDamageGroup(spec.relation),)))
    shares=[]
    for recipient in recipients.recipients:
        amount=resolve_public_query_amount(host,spec.amount.to_dict(),item,query_player=recipient.ref)
        if amount:shares.append(AllocatedDamageRecipient(recipient.ref,None,amount))
    return BoundPerPlayerQueryDamage(item.controller,spec.relation,tuple(shares)).to_dict()
