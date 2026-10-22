from __future__ import annotations

"""Closed current-turn facts shared by activation offers and commit."""

from ...turn_history import current_turn_history_events
from ...activation_condition_model import ActivationConditionKind

ACTIVATION_TURN_EVENT_CAPABILITY='activation.condition.current_turn_events'


def activation_turn_event_status(host,seat,kind):
    if not isinstance(kind,ActivationConditionKind) or kind not in {ActivationConditionKind.CONTROLLER_CAST_INSTANT_SORCERY_THIS_TURN,
            ActivationConditionKind.OPPONENT_LOST_LIFE_THIS_TURN}:
        return 'unresolved','unsupported_activation_turn_event_condition'
    state=host.state;history=state.turn_history;turn=state.turn_sequence
    if history is None or history.schema_version!=1 or history.turn_sequence!=turn:
        return 'unresolved','activation_turn_history_unavailable'
    if kind is ActivationConditionKind.CONTROLLER_CAST_INSTANT_SORCERY_THIS_TURN:
        matched=any(event.actor==seat and set(event.types)&{'instant','sorcery'} for event in
            current_turn_history_events(history,turn_sequence=turn,kind='spell_cast'))
        return ('payable',None) if matched else ('unavailable','requires_own_instant_or_sorcery_cast_this_turn')
    opponents={other for other,player in state.players.items() if other!=seat and player.in_game}
    matched=any(event.target in opponents and event.amount>0 for event in
        current_turn_history_events(history,turn_sequence=turn,kind='player_lost_life'))
    return ('payable',None) if matched else ('unavailable','requires_opponent_lost_life_this_turn')
