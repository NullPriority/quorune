"""Retain only committed discard occurrences of the sealed hand incarnations."""
from ..discard_result_model import PlayerDiscardCounts
from ..whole_hand_discard import whole_hand_input_snapshot
from ..whole_hand_discard_model import DiscardWholeHandsIntent
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap


def discard_result_hand_snapshot(host, actor, effect):
    from ..discard_result_model import (
        DISCARD_GRAVEYARD_ORDER_FIELD, DISCARD_RESULT_OPERATION, SELECTED_LAND_DISCARD_FIELD,
        event_discard_player_departed, is_ordered_opponents_discard, validate_discard_draw,
    )
    if effect.get('op') != DISCARD_RESULT_OPERATION:return None
    if DISCARD_GRAVEYARD_ORDER_FIELD in effect:
        from ..graveyard_arrival_order_model import GraveyardArrivalOrders

        try:
            pending = GraveyardArrivalOrders.from_dict(effect[DISCARD_GRAVEYARD_ORDER_FIELD]["orders"])
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        if pending.current_group is None or pending.current_group.owner != actor:
            raise GameRuleError("The decision actor must be the pending graveyard owner")
        fields = {DISCARD_GRAVEYARD_ORDER_FIELD}
        if is_ordered_opponents_discard(effect):
            fields.add("player")
        effect = {k: v for k, v in effect.items() if k not in fields}
        actor = effect["actor"]
    validate_discard_draw(effect)
    if event_discard_player_departed(effect, seats=host.seats, active_seats=host.active_seats):
        return None
    snapshot = whole_hand_input_snapshot(host, {'op': 'discard_whole_hands', 'actor': actor, 'players': effect['players']}, actor=actor)
    hands = FrozenMap({row['seat']: sorted(row['members'], key=lambda member: member['object_id'])
                       for row in snapshot['hands']})
    if SELECTED_LAND_DISCARD_FIELD not in effect:
        return hands
    selected = tuple(FrozenMap(row) for row in effect[SELECTED_LAND_DISCARD_FIELD])
    if actor != effect['actor'] or any(member not in hands[actor] for member in selected):
        raise GameRuleError('The selected land discard no longer matches the retained hand')
    return FrozenMap({actor: selected})


def _commit_selected_land_discards(sink, intent, current):
    from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
    from ..zone_trigger_events import ZoneTransitionKind

    selections = intent.hands[intent.actor]
    if any(member not in current[intent.actor] for member in selections):
        raise GameRuleError('A selected hand incarnation changed before discard')
    for member in selections:
        card = sink.state.cards[member['object_id']]
        data = sink._effective_card_data(card)
        if 'land' not in sink._type_parts(str(data.get('type_line') or ''))[0]:
            raise GameRuleError('The selected card must still be a land')
    move = MoveObjectsSimultaneouslyIntent(
        actor=intent.actor, object_refs=tuple(member['ref'] for member in selections), expected_zones=('hand',),
        destination='graveyard', reason=intent.reason, transition_kind=ZoneTransitionKind.DISCARD,
        replacement_selections=intent.replacement_selections, owned_only=True,
        discard_effect_origin=intent.discard_effect_origin,
    )
    return sink.move_objects_simultaneously_intent(
        move, graveyard_orders=intent.graveyard_orders, require_graveyard_order=True,
    )


def committed_discard_members(events, members):
    selected = {tuple(member) for member in members}
    return frozenset((event.actor, event.object_incarnation) for event in events
                     if (event.actor, event.object_incarnation) in selected)


def execute_discard_result(sink, intent):
    from ..semantic_runtime.whole_hand_discard import execute_whole_hand_discard
    snapshot = whole_hand_input_snapshot(sink, {'op': 'discard_whole_hands', 'actor': intent.actor, 'players': list(intent.players)}, actor=intent.actor)
    current = FrozenMap({row['seat']: sorted(row['members'], key=lambda member: member['object_id']) for row in snapshot['hands']})
    if not intent.selected_lands and current != intent.hands:
        raise GameRuleError('Retained whole-hand discard membership changed')
    before = len(tuple(sink._current_turn_history('card_discarded')))
    if intent.selected_lands:
        _commit_selected_land_discards(sink, intent, current)
    else:
        execute_whole_hand_discard(
            sink, DiscardWholeHandsIntent(intent.actor, intent.players, intent.reason, intent.replacement_selections,
                                          effect_origin=intent.discard_effect_origin),
            graveyard_orders=intent.graveyard_orders, require_graveyard_order=intent.require_graveyard_order,
        )
    events = tuple(sink._current_turn_history('card_discarded'))[before:]
    return PlayerDiscardCounts(tuple((player, len(committed_discard_members(
        events, ((player, member["logical_object_id"]) for member in intent.hands[player]),
    ))) for player in intent.players))
