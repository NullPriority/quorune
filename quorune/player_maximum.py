"""Read-only current public player metrics; no hidden object identities escape."""
from .player_maximum_model import PlayerMaximumSpec
from .object_query import object_matches_query


def current_player_metric_values(state,spec,*,public_object_resolver=None):
    if not isinstance(spec,PlayerMaximumSpec):raise ValueError('Player maximum requires its typed metric')
    values={}
    for seat in state.turn_order:
        player=state.players.get(seat)
        if player is None or type(getattr(player,'in_game',None)) is not bool:
            raise ValueError('Player maximum participation is unavailable')
        if not player.in_game:continue
        if spec.metric=='life':
            value=player.life
            if type(value) is not int:raise ValueError('Player maximum life total is unavailable')
        elif spec.metric=='hand_count':
            hand=player.zones.get('hand')
            if not isinstance(hand,(list,tuple)):raise ValueError('Player maximum hand size is unavailable')
            value=len(hand)
        else:value=0
        values[seat]=value
    if not values:raise ValueError('Player maximum requires current active players')
    query=spec.predicate()
    if query is not None:
        if public_object_resolver is None:raise ValueError('Player maximum battlefield facts are unavailable')
        for card in state.cards.values():
            if card.zone!='battlefield' or card.phased_out:continue
            if card.controller not in state.players:raise ValueError('Player maximum object controller is unavailable')
            if card.controller not in values:continue
            row=public_object_resolver(card)
            if row is None:raise ValueError('Player maximum object facts are unavailable')
            if object_matches_query(row,query):values[card.controller]+=1
    return tuple(values.items())


def current_players_at_maximum(state, spec, *, public_object_resolver=None):
    values = current_player_metric_values(state, spec, public_object_resolver=public_object_resolver)
    maximum = max(value for _, value in values)
    return tuple(seat for seat, value in values if value == maximum)


def current_player_maximum(state, spec, *, public_object_resolver=None):
    winners = current_players_at_maximum(state, spec, public_object_resolver=public_object_resolver)
    return winners[0] if len(winners) == 1 else None


def opponent_has_greater_metric(state,controller,spec,*,public_object_resolver=None):
    values=dict(current_player_metric_values(state,spec,public_object_resolver=public_object_resolver))
    if controller not in values:raise ValueError('Relative player metric resolver is unavailable')
    return any(value>values[controller] for seat,value in values.items() if seat!=controller)


def resolve_player_maximum(host,value,item):
    from .continuous_effect_model import Layer
    spec=PlayerMaximumSpec.from_reference(value)
    return current_player_maximum(host.state,spec,public_object_resolver=lambda card:
        host._public_object_query_result(card,_maximum_layer=Layer.COLOR))
