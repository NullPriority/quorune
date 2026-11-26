from __future__ import annotations

"""Descriptor-owned capability minima for paid-color self-entry counters."""

from ..entry_counter_model import (
    DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID, PAID_COLOR_ENTRY_CAPABILITY, PAID_GREEN_PAIRS_ENTRY_CAPABILITY,
    DynamicEntryCounterCalculation,
)
from ..spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY


def paid_color_entry_runtime_dependencies(program):
    from ..semantic_runtime.self_entry_counters import DynamicSelfEntryCounterHandler
    handler=DynamicSelfEntryCounterHandler()
    required = set()
    for descriptor in program.handlers:
        if descriptor.get("handler_id") != DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID:
            continue
        node = handler.validate(descriptor)
        if node.amount_spec.mana_color is not None:
            required.update((PAID_COLOR_ENTRY_CAPABILITY, MANA_SPENT_COUNTS_CAPABILITY))
        if node.amount_spec.calculation is DynamicEntryCounterCalculation.PAID_GREEN_PAIRS:
            if node.counter_name != "+1/+1" or node.rule_id != "614.1c":
                raise ValueError("Paid green pairs place only +1/+1 entry counters")
            required.add(PAID_GREEN_PAIRS_ENTRY_CAPABILITY)
    declared = getattr(program, "capability_dependencies", ())
    if PAID_GREEN_PAIRS_ENTRY_CAPABILITY in declared and PAID_GREEN_PAIRS_ENTRY_CAPABILITY not in required:
        raise ValueError("Declared green pairs lost their exact self-entry amount")
    required.update(_paid_white_target_dependencies(program, declared))
    return tuple(sorted(required))


def _paid_white_target_dependencies(program, declared):
    from collections.abc import Mapping

    from ..card_programs.admission import program_has_declared_complete_source_admission
    from ..source_entry_cast_information import (
        PAID_WHITE_ENTRY_TARGET_CAPABILITY, PAID_WHITE_ENTRY_TARGET_MECHANIC, uses_paid_white_entry_amount,
    )
    from .counter_target_set_shapes import quantified_counter_target_capabilities
    from .effect_shape_walkers import nested_effects

    mentions_amount = any(uses_paid_white_entry_amount(effect.get("amount"))
                          for effect in nested_effects(program.effects))
    if (not mentions_amount and PAID_WHITE_ENTRY_TARGET_MECHANIC not in program.coverage
            and PAID_WHITE_ENTRY_TARGET_CAPABILITY not in declared):
        return ()
    if (not mentions_amount or len(program.effects) != 1
            or not uses_paid_white_entry_amount(program.effects[0].get("amount"))
            or program.event != "permanent.enter.self" or program.active_zone != "battlefield"
            or program.event_condition is not None or not isinstance(program.target_schema, Mapping)
            or program.provenance.get("granted_only") or not program_has_declared_complete_source_admission(program)):
        raise ValueError("Paid white pairs require their complete original self-entry trigger")
    required = quantified_counter_target_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Paid white pairs lost their exact other controlled creature target set")
    return required
