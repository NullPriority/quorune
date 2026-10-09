from __future__ import annotations

"""Public death-event card returns through the existing zone mutation owner."""

import re

from ..event_card_return import EVENT_CARD_RETURN_MECHANIC, event_card_return_effect
from ..rules.source_references import SourceReferenceSpec


def self_death_return_binding(material_line: str, *, card_name: str | None = None):
    from .fixed_public_event_trigger_bindings import _spec
    match = re.fullmatch(r'(?:When|Whenever) (?P<source>this creature|.+?) dies, (?P<body>.+)', material_line, re.I)
    if match is None or not (match['source'].casefold() == 'this creature' or card_name and SourceReferenceSpec(card_name).matches(match['source'])):
        return None
    if not re.fullmatch(r"return (?:it|this card|" + SourceReferenceSpec(card_name or '').regex_pattern + r") to its owner['’]s hand\.?", match['body'], re.I):
        return None
    return _spec('creature.dies.self', 'source_death_return', match['body'],
        'fixed-counter-source-death-return-v1', 'trigger-event-normalized-zone-change',
        capabilities=('zone.return.fixed_event_card',))


def event_card_return_template(binding, body: str, *, card_name: str):
    if binding.event.value not in {'creature.dies.self', 'permanent.graveyard.self', 'creature.dies'}:
        return None
    source_event = binding.event.value.endswith('.self') or binding.variant in {'source_creature_dies', 'source_dies'}
    attached = binding.variant in {'enchanted_creature_dies', 'equipped_creature_dies'}
    subject = r'it|this card|' + SourceReferenceSpec(card_name).regex_pattern if source_event else r'that card'
    if not source_event and not attached:
        return None
    if not re.fullmatch(r'return (?:' + subject + r") to its owner['’]s hand\.?", body, re.I):
        return None
    return ('fixed-departed-event-card-return-v1', (event_card_return_effect(),), None, (EVENT_CARD_RETURN_MECHANIC,))
