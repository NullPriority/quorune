from __future__ import annotations

"""Bounded subscriptions to committed battlefield counter placements."""

import re
from ..counter_names import normalized_counter_name
from ..counter_placement_event_model import COUNTER_EVENT_CAPABILITY,COUNTER_EVENT_MECHANIC
from ..rules.source_references import SourceReferenceSpec
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from .qualified_zone_event_bindings import _subject_query,_subject_condition

COUNTER_EVENT_VARIANTS=frozenset({'counter_placement_one_or_more','counter_placement_each_counter'})


def counter_placement_event_binding_spec(material_line,*,card_name=None):
    match=re.fullmatch(r'Whenever (?:(?P<actor>you) put (?P<active_count>one or more) (?:(?P<active_kind>.+?) )?counters on (?P<active_subject>.+?)|'
        r'(?P<passive_count>a|one or more) (?:(?P<passive_kind>.+?) )?counters? (?:is|are) put on (?P<passive_subject>.+?)), (?P<body>.+)',material_line,re.I)
    if match is None:
        return None
    subject=match['active_subject'] or match['passive_subject'];count=match['active_count'] or match['passive_count']
    kind=match['active_kind'] or match['passive_kind']
    if re.search(r'\b(?:first|each turn|one or more|while|tenth)\b',subject,re.I):
        return None
    if kind is not None:
        if not re.fullmatch(r'[+-]\d+/[+-]\d+|[A-Za-z][A-Za-z\'-]*(?: [A-Za-z][A-Za-z\'-]*){0,2}',kind):
            return None
        try:
            kind=normalized_counter_name(kind)
        except ValueError:
            return None
    conditions=[]
    if match['actor']:
        conditions.append({'field':'placing_player','op':'eq','value':'$source.controller'})
    if kind is not None:conditions.append({'field':'counter','op':'eq','value':kind})
    elif count.casefold()=='one or more':conditions.append({'field':'counter_kind_index','op':'eq','value':1})
    source=bool(re.fullmatch(r'this (?:creature|artifact|enchantment|land|permanent)',subject,re.I) or
        (card_name is not None and SourceReferenceSpec(card_name).matches(subject)))
    if source:
        conditions.append({'field':'card','op':'eq','value':'$source.ref'})
    else:
        article=re.fullmatch(r'(?P<article>a|an|another) (?P<body>.+)',subject,re.I)
        if article is None:return None
        parsed=_subject_query(('another target ' if article['article'].casefold()=='another' else 'target ')+article['body'])
        if parsed is None:return None
        query,token=parsed
        if any((query.state_predicate,query.combat_state,query.damage_history,query.commander,query.numeric_characteristic,query.color_count_min,query.color_count_equal)):
            return None
        condition=_subject_condition(query)
        if condition is not None:conditions.append(condition)
        if token is not None:conditions.append({'field':'token','op':'eq','value':token})
    conditions.append({'field':'amount','op':'gte','value':1})
    singular=count.casefold()=='a'
    return FixedPublicEventBindingSpec(event='counter.single_put' if singular else 'counter.put',
        variant='counter_placement_each_counter' if singular else 'counter_placement_one_or_more',body=match['body'],
        template_id='fixed-counter-placement-occurrence-trigger-v1',mechanic=COUNTER_EVENT_MECHANIC,
        condition={'all':conditions},capabilities=(COUNTER_EVENT_CAPABILITY,))
