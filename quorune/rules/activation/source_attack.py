from __future__ import annotations

"""Original-incarnation attack history for one activation condition."""

from ...turn_history import current_turn_history_events


def source_attack_condition_status(host,source):
    if source is None or source.zone!='battlefield':return 'unresolved','activation_source_required'
    history=host.state.turn_history;turn=host.state.turn_sequence
    if history is None or history.schema_version!=1 or history.turn_sequence!=turn:
        return 'unresolved','source_attack_history_unavailable'
    attacked=any(event.object_incarnation==source.logical_object_id for event in
        current_turn_history_events(history,turn_sequence=turn,kind='creature_attacked'))
    return ('payable',None) if attacked else ('unavailable','requires_source_attacked_this_turn')
