"""Source-specific mutation occurrences over the existing public event binder."""

import re
from ..mutate_model import MUTATE_CAPABILITY,MUTATE_EVENT
from .fixed_public_event_trigger_bindings import _spec,_source_subject

MUTATE_SOURCE_VARIANT='source_mutated'
MUTATE_TRIGGER_TEMPLATE='fixed-counter-source-mutate-trigger-v1'


def mutate_event_binding_spec(text,*,card_name=None):
    match=re.fullmatch(r'(?:When|Whenever) (?P<source>.+?) mutates, (?P<body>.+)',text,re.I)
    if match is None or not _source_subject(match['source'],card_name=card_name):return None
    return _spec(MUTATE_EVENT+'.self',MUTATE_SOURCE_VARIANT,match['body'],
        MUTATE_TRIGGER_TEMPLATE,'trigger-event-normalized-public-action',capabilities=(MUTATE_CAPABILITY,))
