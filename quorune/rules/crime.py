"""Commit one crime at authoritative initial stack placement, never retargeting."""
from collections.abc import Mapping
from ..crime_model import CrimeTarget,classify_crime,CRIME_EVENT,CRIME_HISTORY_KIND
from ..errors import GameRuleError


def crime_count_this_turn(state,player):
    history=getattr(state,'turn_history',None)
    if (history is None or history.crime_history_version!=1 or history.turn_sequence!=state.turn_sequence):return None
    return sum(event.actor==player and event.kind==CRIME_HISTORY_KIND for event in history.events)


def record_crime_for_stack_placement(host,item,*,trigger_batch=None):
    history=getattr(host.state,'turn_history',None)
    if history is None or history.crime_history_version!=1:return None
    if item.kind not in {'spell','activated_ability','triggered_ability'}:return None
    if not item.targets:return None
    if history.turn_sequence==host.state.turn_sequence and any(event.kind==CRIME_HISTORY_KIND
            and event.target==item.stack_id for event in history.events):return None
    snapshots=item.context.get('target_snapshots')
    if not isinstance(snapshots,Mapping):raise GameRuleError('Crime classification requires sealed initial target facts')
    try:
        targets=tuple(CrimeTarget.from_target_snapshot(snapshots[ref]) for ref in dict.fromkeys(item.targets))
        fact=classify_crime(actor=item.controller,stack_identity=item.stack_id,stack_ref=item.ref,
            action_kind=item.kind,targets=targets,participants=tuple(host.state.players))
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if fact is None:return None
    host._record_turn_history(CRIME_HISTORY_KIND,actor=fact.player,target=fact.stack_identity,target_kind='stack_object',amount=1)
    host._dispatch_semantic_event(CRIME_EVENT,{**fact.to_context(),
        'active_player':host.state.active_player,
        'crime_count_this_turn':crime_count_this_turn(host.state,fact.player)},trigger_batch=trigger_batch)
    return fact
