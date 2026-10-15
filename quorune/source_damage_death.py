from __future__ import annotations

"""Match committed current-turn damage to the exact departing creature."""

from .turn_history import current_turn_history_events
from .errors import GameRuleError

SOURCE_DAMAGE_DEATH_FIELD = 'source_dealt_damage_to_dead_incarnation_this_turn'
SOURCE_DAMAGE_DEATH_CAPABILITY = 'trigger.event.source_damage_death_history'


def source_damage_death_matches(host, source, context):
    target = context.get('card_object_identity')
    if type(target) is not str or not target:
        raise GameRuleError('Damage-qualified death requires sealed departing incarnation')
    history = host.state.turn_history
    if history is None or history.schema_version != 1 or history.turn_sequence != host.state.turn_sequence:
        raise GameRuleError('Damage-qualified death requires current typed damage history')
    return any(event.object_incarnation == source.logical_object_id
        and event.target_object_incarnation == target and event.amount > 0
        for event in current_turn_history_events(history, turn_sequence=host.state.turn_sequence,
            kind='permanent_damaged'))
