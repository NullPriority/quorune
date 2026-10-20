from __future__ import annotations

"""Descriptor-owned capability minima for paid-color self-entry counters."""

from ..entry_counter_model import DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID, PAID_COLOR_ENTRY_CAPABILITY
from ..spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY


def paid_color_entry_runtime_dependencies(program):
    from ..semantic_runtime.self_entry_counters import DynamicSelfEntryCounterHandler
    handler=DynamicSelfEntryCounterHandler()
    for descriptor in program.handlers:
        if descriptor.get('handler_id')!=DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID:continue
        if handler.validate(descriptor).amount_spec.mana_color is not None:
            return (PAID_COLOR_ENTRY_CAPABILITY,MANA_SPENT_COUNTS_CAPABILITY)
    return ()
