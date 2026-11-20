"""Bind a numeric public query to its legal player target through existing owners."""
from ..target_player_query_amount_model import TargetPlayerQueryAmountSpec
from ..query_effect_amount_model import PublicQueryAmountError
from .query_effect_amounts import resolve_public_query_amount


def resolve_target_player_query_amount(host,value,item):
    spec=TargetPlayerQueryAmountSpec.from_dict(value)
    from .values import resolve_semantic_value
    try:player=resolve_semantic_value(host,f'$target.{spec.target_index}',item)
    except IndexError as exc:raise PublicQueryAmountError('Target public query has no original player target') from exc
    if type(player) is not str or player not in host.state.active_seats():
        raise PublicQueryAmountError('Target public query requires its current legal player target')
    return resolve_public_query_amount(host,spec.amount.to_dict(),item,query_player=player)
