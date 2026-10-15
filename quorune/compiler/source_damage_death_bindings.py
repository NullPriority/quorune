from __future__ import annotations

import re
from ..rules.source_references import SourceReferenceSpec
from ..source_damage_death import SOURCE_DAMAGE_DEATH_CAPABILITY, SOURCE_DAMAGE_DEATH_FIELD
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec


SOURCE_DAMAGE_DEATH_VARIANT = 'source_damage_death_history'


def source_damage_death_binding(text, *, card_name):
    source = SourceReferenceSpec(card_name).regex_pattern if card_name else r'(?!)'
    match = re.fullmatch(r'Whenever a creature(?P<flying> with flying)? dealt damage by '
        rf'(?:this creature|{source}) this turn dies, (?P<body>.+)', text, re.I)
    if match is None:
        return None
    condition = {'field': SOURCE_DAMAGE_DEATH_FIELD, 'op': 'eq', 'value': True}
    if match['flying']:
        condition = {'all': [condition, {'field': 'keywords', 'op': 'contains_any', 'value': ['flying']}]}
    return FixedPublicEventBindingSpec(event='creature.dies', variant=SOURCE_DAMAGE_DEATH_VARIANT,
        body=match['body'], template_id='fixed-counter-source-damage-death-trigger-v1',
        mechanic='trigger-event-normalized-zone-change', condition=condition,
        capabilities=(SOURCE_DAMAGE_DEATH_CAPABILITY,))
