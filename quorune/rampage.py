from __future__ import annotations

"""CR 702.23 uses current blockers once at resolution of each instance."""

from .combat import current_attacker_blocker_count


def rampage_occurrence_drafts(event):
    from .ability_fragments import CombatKeywordTriggerKind
    blocked = event.blocked_attacker_ids
    return [(spec.kind, source, source, spec.amount, index)
        for source in event.participants if source.object_id in blocked
        for index, spec in enumerate(s for s in source.trigger_specs if s.kind is CombatKeywordTriggerKind.RAMPAGE)]


def rampage_resolution_bonus(host, occurrence):
    card = host.state.cards.get(occurrence.source.object_id)
    if (card is None or card.zone != 'battlefield' or card.phased_out
            or card.logical_object_id != occurrence.source.logical_object_id):
        return 0
    count = current_attacker_blocker_count(host.state, card.object_id)
    return max(0, count - 1) * occurrence.amount
