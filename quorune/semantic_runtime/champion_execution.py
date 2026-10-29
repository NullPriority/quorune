"""Route typed Champion requests through canonical rules mutation owners."""
from __future__ import annotations

from .champion_intent import ChampionExileIntent, ReturnLinkedAbilityExileIntent, CHAMPION_INTENT_TYPES


def execute_champion_intent(sink, intent):
    from ..champion_state import exile_champion_permanent, sacrifice_champion_source_if_controlled
    from ..linked_ability_exile_state import return_linked_ability_exile
    if isinstance(intent, ReturnLinkedAbilityExileIntent):
        return intent.actor, return_linked_ability_exile(sink, source=intent.source,
            ability_pair=intent.ability_pair, actor=intent.actor, reason=intent.reason,
            replacement_selections=intent.replacement_selections)
    if not isinstance(intent, ChampionExileIntent):
        raise TypeError('Unknown Champion intent')
    if intent.subject is None:
        return intent.actor, sacrifice_champion_source_if_controlled(sink,
            source=intent.source, actor=intent.actor, reason=intent.reason,
            replacement_selections=intent.replacement_selections)
    return intent.actor, exile_champion_permanent(sink, source=intent.source,
        ability_pair=intent.ability_pair, actor=intent.actor, subject=intent.subject,
        subject_ref=intent.subject_ref, spec=intent.spec, reason=intent.reason,
        replacement_selections=intent.replacement_selections)
