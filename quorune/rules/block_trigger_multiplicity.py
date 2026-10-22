from __future__ import annotations

from collections.abc import Mapping


def _uses_block_pair(condition):
    if isinstance(condition, Mapping):
        return condition.get('field') in {'blocked_attacker', 'blocked_attacker_keywords'} or any(
            _uses_block_pair(value) for value in condition.values())
    if isinstance(condition, (list, tuple)):
        return any(_uses_block_pair(value) for value in condition)
    return False


def block_trigger_already_collected(program_key, source_id, context, selected_condition, batch):
    """CR509.3a/b: one blocking event, or one event per represented pair."""
    if not context.get('event_id') or not batch or _uses_block_pair(selected_condition):
        return False
    return any(item.semantic_key == program_key and item.source_object_id == source_id
        and item.context.get('event') == 'creature.blocks'
        and item.context.get('event_id') == context['event_id']
        and item.context.get('card') == context.get('card') for item in batch)
