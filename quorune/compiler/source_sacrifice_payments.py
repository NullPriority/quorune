"""Resolve a source-cost anaphor only when every occurrence proves its subject."""

from collections.abc import Mapping
import re

from ..rules.event_subscriptions import FixedEventSubscriptionSet
from ..rules.source_references import SourceReferenceSpec


def _guarantees(condition, field):
    if not isinstance(condition, Mapping):
        return False
    if condition == {'field':field, 'op':'eq', 'value':'$source.ref'}:
        return True
    if set(condition)=={'all'} and isinstance(condition['all'],(list,tuple)):
        return any(_guarantees(child,field) for child in condition['all'])
    if set(condition)=={'any'} and isinstance(condition['any'],(list,tuple)) and condition['any']:
        return all(_guarantees(child,field) for child in condition['any'])
    return False


def _source_subject(binding):
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(binding.event_condition)
    except (TypeError,ValueError):
        return False
    occurrences=((binding.event.value,binding.event_condition),) if subscriptions is None else tuple(
        (entry.event,entry.condition) for entry in subscriptions.subscriptions)
    if binding.event.value not in {event for event,_condition in occurrences}:
        return False
    return all(event.endswith('.self') or _guarantees(condition,
        'target' if event in {'damage.received','combat.damage.received'} else 'card')
        or event=='damage.dealt' and _guarantees(condition,'source') for event,condition in occurrences)


def normalized_source_sacrifice_payment_text(binding, text, *, card_name):
    match=re.fullmatch(r'(?P<head>You may sacrifice) (?P<object>[^.]+)\. If you do, (?P<body>.+)',text,re.I)
    if match is None:
        return text
    subject=match['object'].casefold()
    if subject in {'it','him','her'}:
        if not _source_subject(binding):
            return text
    elif not card_name or not SourceReferenceSpec(card_name).matches(match['object']):
        return text
    return match['head']+' this permanent. If you do, '+match['body']
