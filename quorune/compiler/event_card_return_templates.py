from __future__ import annotations

"""Public death-event card returns through the existing zone mutation owner."""

import re

from ..event_card_return import (EVENT_CARD_RETURN_MECHANIC, event_card_return_effect,
    EVENT_BATTLEFIELD_RETURN_MECHANIC, event_battlefield_return_effect)
from ..rules.source_references import SourceReferenceSpec


def self_death_return_binding(material_line: str, *, card_name: str | None = None):
    from .fixed_public_event_trigger_bindings import _spec
    match = re.fullmatch(r'(?:When|Whenever) (?P<source>this creature|.+?) dies, (?P<body>.+)', material_line, re.I)
    if match is None or not (match['source'].casefold() == 'this creature' or card_name and SourceReferenceSpec(card_name).matches(match['source'])):
        return None
    subject = r"it|this card"
    if card_name:
        subject += "|" + SourceReferenceSpec(card_name).regex_pattern
    if not re.fullmatch(r"return (?:" + subject + r") to (?:its owner['’]s hand|the battlefield "
            r"(?:tapped )?under its owner['’]s control(?: with a \+1/\+1 counter on it)?)\.?", match['body'], re.I):
        return None
    battlefield = 'the battlefield' in match['body'].casefold()
    return _spec('creature.dies.self', 'source_death_return', match['body'],
        'fixed-counter-source-death-return-v1', 'trigger-event-normalized-zone-change',
        capabilities=('zone.return.fixed_event_battlefield' if battlefield else 'zone.return.fixed_event_card',))


def event_card_return_template(binding, body: str, *, card_name: str):
    attached_graveyard = (binding.event.value == 'permanent.graveyard'
                          and binding.variant == 'enchanted_permanent_graveyard')
    if binding.event.value not in {'creature.dies.self', 'permanent.graveyard.self', 'creature.dies'} and not attached_graveyard:
        return None
    source_event = binding.event.value.endswith('.self') or binding.variant in {'source_creature_dies', 'source_dies'}
    attached = binding.variant in {'enchanted_creature_dies', 'equipped_creature_dies'}
    if not (source_event or attached or attached_graveyard):
        return None
    if (attached_graveyard or attached and body.casefold().startswith('return it ')) and body != binding.body:
        return None
    subject = r'it|this card|' + SourceReferenceSpec(card_name).regex_pattern if source_event else r'it|that card'
    battlefield = re.fullmatch(r'return (?:' + subject + r") to the battlefield "
        r"(?P<tapped>tapped )?under (?P<controller>its owner['’]s|your) control"
        r"(?P<counter> with a \+1/\+1 counter on it)?\.?", body, re.I)
    if battlefield is not None:
        return_to_controller = battlefield['controller'].casefold() == 'your'
        if attached_graveyard or return_to_controller and (not attached or body != binding.body):
            return None
        return ('fixed-departed-event-battlefield-return-v1',
            (event_battlefield_return_effect(tapped=battlefield['tapped'] is not None,
                entry_counter='+1/+1' if battlefield['counter'] else None,
                controller='$controller' if return_to_controller else '$context.owner'),), None,
            (EVENT_BATTLEFIELD_RETURN_MECHANIC,))
    if not re.fullmatch(r'return (?:' + subject + r") to its owner['’]s hand\.?", body, re.I):
        return None
    return ('fixed-departed-event-card-return-v1', (event_card_return_effect(),), None, (EVENT_CARD_RETURN_MECHANIC,))
