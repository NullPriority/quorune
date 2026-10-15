from __future__ import annotations

"""Pay fixed energy through the canonical player counter transaction."""

from .semantic_runtime.energy_payment import PayEnergyIntent


def execute_energy_payment(sink, intent: PayEnergyIntent):
    from .counter_state import CounterChange, plan_counter_changes, commit_counter_changes
    from .errors import GameRuleError
    player = sink.state.players.get(intent.player)
    if player is None or type(player.energy) is not int or player.energy < intent.amount:
        raise GameRuleError('The energy payment is no longer payable')
    plan = plan_counter_changes(sink, (CounterChange('player', intent.player, 'energy', -intent.amount),))
    result = commit_counter_changes(sink, plan)
    sink._log(intent.actor, 'energy.pay', f'{intent.player} paid {intent.amount} energy.',
        {'player': intent.player, 'amount': intent.amount, 'reason': intent.reason},
        importance=1, changed_players=[intent.player])
    return result
