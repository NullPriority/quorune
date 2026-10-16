from __future__ import annotations

"""Current half-life loss through the canonical simultaneous life owner."""

from ..errors import GameRuleError
from ..fractional_quantity_model import HalfQuantitySpec
from ..life_state import LifeChange


def apply_fractional_life_loss(host, effect, *, actor, operation, reason):
    from .life_effects import _commit, _result

    try:
        fraction = HalfQuantitySpec.from_dict(effect['amount'])
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if 'scope' in effect:
        if 'player' in effect or effect['scope'] not in {'all_active_players', 'opponents'}:
            raise GameRuleError('Fractional life player domain is contradictory')
        players = tuple(seat for seat in host.active_seats if effect['scope'] == 'all_active_players' or seat != actor)
    else:
        player = effect.get('player')
        if type(player) is not str or player not in host.active_seats:
            raise GameRuleError('Fractional life requires an active represented player')
        players = (player,)
    changes = tuple(LifeChange(seat, -fraction.amount(host.state.players[seat].life)) for seat in players)
    prepared = _commit(host, changes, effect=effect, actor=actor, reason=reason)
    result = _result(prepared)
    host._log(actor, 'effect.life', 'Fractional life loss resolved.',
        {'players': list(players), 'deltas': {seat: result.for_player(seat).delta for seat in players},
         'fraction': fraction.to_dict(), **result.to_dict()}, importance=2, changed_players=list(result.changed_players))
    return sum(result.for_player(seat).resolved_loss for seat in players)


def apply_life_loss(host, effect, *, actor, operation, reason):
    from collections.abc import Mapping
    from .life_effects import _apply_lose_life

    if isinstance(effect.get('amount'), Mapping):
        return apply_fractional_life_loss(host, effect, actor=actor, operation=operation, reason=reason)
    return _apply_lose_life(host, effect, actor=actor, operation=operation, reason=reason)
