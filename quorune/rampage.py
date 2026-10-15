from __future__ import annotations

"""CR 702.23 uses current blockers once at resolution of each instance."""


def rampage_occurrence_drafts(event):
    from .ability_fragments import CombatKeywordTriggerKind
    blocked = {assignment.attacker_object_id for assignment in event.assignments}
    return [(spec.kind, source, source, spec.amount, index)
        for source in event.participants if source.object_id in blocked
        for index, spec in enumerate(s for s in source.trigger_specs if s.kind is CombatKeywordTriggerKind.RAMPAGE)]


def rampage_resolution_bonus(host, occurrence):
    card = host.state.cards.get(occurrence.source.object_id)
    if (card is None or card.zone != 'battlefield' or card.phased_out
            or card.logical_object_id != occurrence.source.logical_object_id):
        return 0
    blockers = host.state.combat.blockers.get(card.object_id, ())
    count = sum(1 for oid in dict.fromkeys(blockers) if (blocker := host.state.cards.get(oid)) is not None
        and blocker.zone == 'battlefield' and not blocker.phased_out and blocker.blocking is not None)
    return max(0, count - 1) * occurrence.amount
