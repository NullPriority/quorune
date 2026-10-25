from __future__ import annotations

"""Closed counted public facts for the existing intrinsic spell-price owner."""

from collections.abc import Mapping

from ..drawing import drawn_this_turn


CAPABILITY = 'casting.cost.self_public_turn_count'
COUNTED_TURN_FACTS = frozenset({'creature_death_count', 'opponent_spell_cast_count', 'controller_card_draw_count'})


def self_cost_turn_count(host, seat, fact):
    value = fact.value
    if value == 'creature_death_count':
        return len(host._current_turn_history('creature_died'))
    if value == 'opponent_spell_cast_count':
        opponents = {key for key, player in host.state.players.items() if key != seat and player.in_game}
        return sum(event.actor in opponents for event in host._current_turn_history('spell_cast'))
    if value == 'controller_card_draw_count':
        return drawn_this_turn(host, seat)
    raise ValueError('Unrepresented counted self-cost turn fact')


def self_cost_turn_count_dependencies(value):
    if isinstance(value, Mapping):
        own = ()
        if value.get('turn_fact') in COUNTED_TURN_FACTS:
            from ..self_cast_reductions import CastReductionMetric
            CastReductionMetric.from_dict(value)
            own = (CAPABILITY,)
        return tuple(sorted({*own, *(cap for child in value.values() for cap in self_cost_turn_count_dependencies(child))}))
    if isinstance(value, (list, tuple)):
        return tuple(sorted({cap for child in value for cap in self_cost_turn_count_dependencies(child)}))
    return ()
