from __future__ import annotations

"""Narrow current-source capture for an external entry-counter replacement."""

from ..external_entry_counter_model import (
    EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_HANDLER, ExternalEntryCounterSpec, ExternalEntryCounterSourceSnapshot,
)
from .chosen_type_events import chosen_type_event_value


def snapshot_external_entry_counter_source(host,source,descriptor):
    spec=ExternalEntryCounterSpec.from_dict(descriptor['entry_counter'])
    if source.zone!='battlefield' or source.phased_out:
        raise ValueError('External entry-counter source is not currently phased in on the battlefield')
    if spec.amount_source=='fixed_one':amount=1
    elif spec.amount_source=='source_plus_one_counters':amount=source.counters.get('+1/+1',0)
    else:amount=host._numeric_stat(source.object_id,'power' if spec.amount_source=='source_power' else 'toughness')
    chosen = (chosen_type_event_value(source)
              if descriptor.get("handler_id") == EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_HANDLER else None)
    return ExternalEntryCounterSourceSnapshot(
        source.object_id, source.logical_object_id, source.controller, amount, chosen,
    )
