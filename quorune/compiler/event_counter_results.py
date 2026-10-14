from __future__ import annotations

"""Whole fixed counter result on one typed entry occurrence."""

import re

EVENT_COUNTER_CAPABILITY='counter.producer.fixed_event_object'
EVENT_COUNTER_MECHANIC='fixed-entry-event-object-counter'
EVENT_ENTRY_OBJECT='$event.entry_object'


def entry_event_counter_result(binding,body,*,effect_template,card_name):
    if binding.event.value not in {'permanent.enter','creature.enter','artifact.enter','enchantment.enter','land.enter'}:return None
    if binding.variant.startswith('one_or_more') or binding.variant in {'aggregated_zone_event','independent_dual_event'}:return None
    match=re.fullmatch(r'(?P<head>Put .+? counters? on) (?P<ref>it|that creature|that artifact|that permanent)\.',body,re.I)
    if match is None:return None
    compiled=effect_template(match['head']+' this creature.',card_name=card_name)
    if compiled[0] is None or compiled[2] is not None or len(compiled[1])!=1:return None
    effect=compiled[1][0]
    if effect.get('op')!='place_counters' or effect.get('card') not in {'$source','$source.zone_object'}:return None
    return ('fixed-entry-event-object-counter-v1',({**effect,'card':EVENT_ENTRY_OBJECT},),None,
        (*compiled[3],EVENT_COUNTER_MECHANIC)),True
