"""Retain only committed discard occurrences of the sealed hand incarnations."""
from ..discard_result_model import PlayerDiscardCounts
from ..whole_hand_discard import whole_hand_input_snapshot
from ..whole_hand_discard_model import DiscardWholeHandsIntent
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap


def discard_result_hand_snapshot(host, actor, effect):
    from ..discard_result_model import DISCARD_RESULT_OPERATION, validate_discard_draw
    if effect.get('op') != DISCARD_RESULT_OPERATION:return None
    validate_discard_draw(effect)
    snapshot = whole_hand_input_snapshot(host, {'op': 'discard_whole_hands', 'actor': actor, 'players': effect['players']}, actor=actor)
    return FrozenMap({row['seat']: sorted(row['members'], key=lambda member: member['object_id']) for row in snapshot['hands']})


def execute_discard_result(sink, intent):
    from ..semantic_runtime.whole_hand_discard import execute_whole_hand_discard
    snapshot = whole_hand_input_snapshot(sink, {'op': 'discard_whole_hands', 'actor': intent.actor, 'players': list(intent.players)}, actor=intent.actor)
    current = FrozenMap({row['seat']: sorted(row['members'], key=lambda member: member['object_id']) for row in snapshot['hands']})
    if current != intent.hands:
        raise GameRuleError('Retained whole-hand discard membership changed')
    before = len(tuple(sink._current_turn_history('card_discarded')))
    execute_whole_hand_discard(sink, DiscardWholeHandsIntent(intent.actor, intent.players, intent.reason, intent.replacement_selections))
    events = tuple(sink._current_turn_history('card_discarded'))[before:]
    return PlayerDiscardCounts(tuple((player, len({event.object_incarnation for event in events
        if event.actor == player and event.object_incarnation in {member['logical_object_id'] for member in intent.hands[player]}})) for player in intent.players))
