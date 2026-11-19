from __future__ import annotations

"""Hand return/redraw transactions delegated to existing zone and draw owners."""

from collections.abc import Mapping
from ..hand_library_redraw_model import (
    HAND_LIBRARY_REDRAW_OPERATION as OP, HAND_LIBRARY_REDRAW_DEPENDENCIES as DEPENDENCIES,
    HAND_LIBRARY_REDRAW_MECHANIC as MECHANIC, HandLibraryScope as Scope,
    HandLibraryPlacement as Placement, HandLibraryRedrawIntent, HandLibraryArrivalOrder,
    HandLibraryRedrawSpec, PlayerLibraryReturnCounts, decode_hand_library_redraw,
    committed_library_return_counts,
)
from ..replacement.immutable import FrozenMap
from ..whole_hand_discard import whole_hand_input_snapshot

_BASE_FIELDS = frozenset({'op', 'actor', 'player', 'redraw'})
_CONTINUATION_FIELDS = frozenset({'_hands', '_selected', '_choice_actor', '_hand_incarnations', '_stack_label'})


def hand_library_instruction(effect):
    if (not isinstance(effect, Mapping) or not _BASE_FIELDS <= set(effect)
            or set(effect) - _BASE_FIELDS - _CONTINUATION_FIELDS):
        raise ValueError('Hand redraw has unknown compiled or continuation fields')
    return {key: effect[key] for key in _BASE_FIELDS}


def hand_library_redraw_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    if target_schema is not None or len(effects) != 1 or MECHANIC not in mechanic_ids:
        return ()
    try:
        spec = decode_hand_library_redraw(effects[0])
    except (TypeError, ValueError):
        return ()
    return _redraw_dependencies(spec)


def _redraw_dependencies(spec):
    return (*DEPENDENCIES, *(('trigger.event.scheduled_player_result',) if spec.scope is Scope.EVENT_PLAYER else ()))


def hand_library_redraw_payload_dependencies(value):
    if value.get('op') != OP:
        return ()
    return _redraw_dependencies(decode_hand_library_redraw(value))


def is_closed_hand_library_redraw_program(program):
    required = hand_library_redraw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    if not required or not set(required) <= set(program.capability_dependencies):
        return False
    spec = HandLibraryRedrawSpec.from_dict(program.effects[0]['redraw'])
    if spec.scope is Scope.EVENT_PLAYER:
        from .scheduled_player_shapes import scheduled_player_result_step, SCHEDULED_PLAYER_CAPABILITY
        return (scheduled_player_result_step(program) == 'draw'
                and SCHEDULED_PLAYER_CAPABILITY in program.capability_dependencies)
    return True


def hand_library_redraw_hand_snapshot(host, chooser, effect):
    if effect.get('op') != OP:
        return None
    base = hand_library_instruction(effect)
    spec = decode_hand_library_redraw(base, actor=base['actor'], player=chooser)
    if base['actor'] not in host.active_seats:
        raise ValueError('Hand redraw resolving controller is unavailable')
    if spec.scope is Scope.EVENT_PLAYER and chooser not in host.active_seats:
        if chooser not in host.state.players:
            raise ValueError('Hand redraw recipient is unknown')
        return FrozenMap()
    players = tuple(host.apnap_order()) if spec.scope is Scope.ALL_PLAYERS else (chooser,)
    snapshot = whole_hand_input_snapshot(host, {
        'op':'discard_whole_hands', 'actor':base['actor'], 'players':list(players),
    }, actor=base['actor'])
    return FrozenMap({row['seat']:sorted(row['members'],key=lambda member:member['object_id'])
                      for row in snapshot['hands']})


def result_hand_snapshot(host, chooser, effect):
    if effect.get('op') == OP:
        return hand_library_redraw_hand_snapshot(host, chooser, effect)
    from .discard_results import discard_result_hand_snapshot
    return discard_result_hand_snapshot(host, chooser, effect)


def execute_hand_library_redraw(host, intent):
    from ..errors import GameRuleError
    from ..replacement_effects import ReplacementChoiceRequired
    try:
        return _execute_hand_library_redraw(host, intent)
    except ReplacementChoiceRequired:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc


def _execute_hand_library_redraw(host, intent):
    if not isinstance(intent, HandLibraryRedrawIntent):
        raise ValueError('Hand redraw requires its typed private return intent')
    if intent.actor not in host.active_seats:
        raise ValueError('Hand redraw controller is no longer active')
    players = tuple(seat for seat in intent.players if seat in host.active_seats)
    if intent.redraw.scope is Scope.ALL_PLAYERS and tuple(host.apnap_order()) != players:
        raise ValueError('Hand redraw changed its surviving participant order')
    # Check complete source hands before any price-free library movement.
    snapshot = whole_hand_input_snapshot(host, {
        'op':'discard_whole_hands', 'actor':intent.actor, 'players':list(players),
    }, actor=intent.actor)
    current = FrozenMap({row['seat']:sorted(row['members'],key=lambda member:member['object_id'])
                         for row in snapshot['hands']})
    if any(current[seat] != intent.hands[seat] for seat in players):
        raise ValueError('Hand redraw source identities changed before committing')
    refs = tuple(ref for seat in players for ref in intent.selected[seat])
    if refs:
        from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
        order = None
        if intent.redraw.placement is Placement.BOTTOM:
            if len(players) != 1:
                raise ValueError('Private bottom placement requires one library owner')
            order = HandLibraryArrivalOrder(players[0], 'bottom', refs)
        host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=intent.actor, object_refs=refs, expected_zones=('hand',), destination='library',
            reason=intent.reason, replacement_selections=intent.replacement_selections, library_order=order))
    elif intent.replacement_selections:
        raise ValueError('An empty hand return has no per-object replacement selections')
    active_hands = FrozenMap({seat:intent.hands[seat] for seat in players})
    active_selected = FrozenMap({seat:intent.selected[seat] for seat in players})
    counts = (committed_library_return_counts(
        HandLibraryRedrawIntent(intent.actor,players,intent.redraw,active_hands,active_selected,intent.reason),
        {seat:tuple(host.state.players[seat].zones['library']) for seat in players})
        if players else PlayerLibraryReturnCounts(()))
    if intent.redraw.placement is Placement.SHUFFLE:
        for seat in players:
            host.shuffle_library(seat, reason=intent.reason)
    return counts
