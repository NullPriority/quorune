from __future__ import annotations

"""Current CR122 counters for one source-pinned spell price, before cost lock."""

from collections.abc import Mapping

from ..counter_snapshot import COUNTER_TOTAL_CAPABILITY, permanent_counter_total
from ..counter_state import player_counter_snapshot


CAPABILITY = 'casting.cost.self_public_counter_total'
METRIC_KIND = 'public_player_permanent_counter_total'


def public_player_permanent_counter_total(host):
    active = {seat for seat, player in host.state.players.items() if player.in_game}
    players = sum(sum(player_counter_snapshot(host.state.players[seat]).values()) for seat in active)
    permanents = sum(permanent_counter_total(card.counters) for card in host.state.cards.values()
        if card.zone == 'battlefield' and card.controller in active and not card.phased_out)
    return players + permanents


def self_cost_public_counter_dependencies(value):
    if isinstance(value, Mapping):
        own = ()
        if value.get('kind') == METRIC_KIND:
            from ..self_cast_reductions import CastReductionMetric
            CastReductionMetric.from_dict(value)
            own = (CAPABILITY, COUNTER_TOTAL_CAPABILITY)
        return tuple(sorted({*own, *(cap for child in value.values() for cap in self_cost_public_counter_dependencies(child))}))
    if isinstance(value, (list, tuple)):
        return tuple(sorted({cap for child in value for cap in self_cost_public_counter_dependencies(child)}))
    return ()
