"""Exact source and public-creature permanent designation trigger heads."""
import re
from ..designation_event_model import (DESIGNATION_EVENT_CAPABILITY, DESIGNATION_EVENT_MECHANIC,
                                       MONSTROUS_EVENT, RENOWNED_EVENT, DESIGNATION_EVENTS)
from ..rules.source_references import SourceReferenceSpec

DESIGNATION_EVENT_VARIANTS = frozenset({'source_becomes_monstrous', 'source_becomes_renowned',
                                       'controlled_creature_becomes_renowned', 'creature_becomes_renowned'})


def designation_event_binding_spec(text, *, card_name=None):
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
    match = re.fullmatch(r'(?:When|Whenever) (?P<subject>.+?) becomes (?P<kind>monstrous|renowned), (?P<body>.+)', text, re.I)
    if match is None:
        return None
    subject = match['subject'].casefold()
    kind = match['kind'].casefold()
    self_reference = subject in {'this creature', 'this permanent'} or bool(card_name and SourceReferenceSpec(card_name).matches(match['subject']))
    if self_reference:
        variant = 'source_becomes_' + kind
        condition = {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
    elif kind == 'renowned' and subject in {'a creature', 'a creature you control'}:
        variant = 'controlled_creature_becomes_renowned' if subject.endswith('you control') else 'creature_becomes_renowned'
        conditions = [{'field': 'types', 'op': 'contains_any', 'value': ['creature']}]
        if subject.endswith('you control'):
            conditions.append({'field': 'controller', 'op': 'eq', 'value': '$source.controller'})
        condition = {'all': conditions}
    else:
        return None
    event = MONSTROUS_EVENT if kind == 'monstrous' else RENOWNED_EVENT
    return FixedPublicEventBindingSpec(event=event + '.self' if self_reference else event, variant=variant, body=match['body'],
        template_id='fixed-counter-public-designation-trigger-v1', mechanic=DESIGNATION_EVENT_MECHANIC,
        condition=condition, capabilities=(DESIGNATION_EVENT_CAPABILITY, DESIGNATION_EVENTS[event]))
