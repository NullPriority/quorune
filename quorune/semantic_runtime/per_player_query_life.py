"""Read all public quantities before a canonical life batch can prepare changes."""
from ..life_state import LifeChange
from ..per_player_query_life_model import (
    PerPlayerQueryLifeLossSpec,BoundPerPlayerLifeLoss,PlayerLifeLossRelation,
    BOUND_PER_PLAYER_QUERY_LIFE_KIND,
)
from .context import ReadOnlyRulesQuery
from .query_effect_amounts import resolve_public_query_amount


def resolve_per_player_query_life(host,value,item):
    if value.get('kind')==BOUND_PER_PLAYER_QUERY_LIFE_KIND:
        bound=BoundPerPlayerLifeLoss.from_dict(value)
        if bound.controller!=item.controller:raise ValueError('Bound per-player loss controller changed')
        return bound.to_dict()
    spec=PerPlayerQueryLifeLossSpec.from_dict(value)
    active=tuple(host.active_seats);order=tuple(host.apnap_order())
    query=ReadOnlyRulesQuery(active,active,order);query.require_active_seat(item.controller)
    changes=[]
    for player in order:
        if spec.relation is PlayerLifeLossRelation.OPPONENTS and player==item.controller:continue
        quantity = resolve_public_query_amount(host, spec.amount.to_dict(), item, query_player=player)
        loss = spec.life_loss_for_quantity(quantity)
        if loss:
            changes.append(LifeChange(player, -loss))
    return BoundPerPlayerLifeLoss(item.controller,spec.relation,tuple(changes)).to_dict()
