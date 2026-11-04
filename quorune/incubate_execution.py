from __future__ import annotations

"""Incubate delegates its one creation to the canonical entry transaction."""

from .defined_token_faces import DEFINED_TOKEN_FACES_FIELD,incubator_definition
from .entry_counter_model import EffectEntryCounter
from .incubate_model import IncubateIntent
from .replacement.immutable import thaw_value
from .token_creation import create_tokens


def execute_incubate_intent(host,intent:IncubateIntent):
    if not isinstance(intent,IncubateIntent):raise ValueError('Expected an Incubate intent')
    counters = (EffectEntryCounter('+1/+1',intent.amount,intent.actor,intent.source_ref,'701.53a'),) if intent.amount else ()
    return tuple(create_tokens(host,intent.controller,name='Incubator',quantity=1,
        characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()},
        effect_entry_counters=counters,reason=intent.reason,
        replacement_selections=tuple(value if isinstance(value,str) else thaw_value(value)
            for value in intent.replacement_selections)))
