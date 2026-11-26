from __future__ import annotations

"""Current typed stack counter results can supersede identical reviewed views."""

from ..delayed_triggers import FIXED_NEXT_UPKEEP_PAYMENT_CAPABILITY, fixed_next_upkeep_payment_cost
from ..replacement.immutable import thaw_value
from ..rules.stack_counter_followup import (
    STACK_MANA_VALUE_FOLLOWUP_CAPABILITY,
    StackManaValueResult,
    stack_mana_value_followup,
)
from .current_view_equivalence import _same_current_source


def _reviewed_counter_and_colorless_mana(effects):
    if len(effects) != 2:
        return False
    counter, delayed = thaw_value(effects)
    if counter != {"op": "counter_stack", "stack": "$target.0"} or not isinstance(delayed, dict):
        return False
    label = delayed.pop("label", None)
    return isinstance(label, str) and bool(label) and delayed == {
        "op": "delayed_mana",
        "player": "$controller",
        "color": "C",
        "amount": "$target.mana_value.0",
    }


def shadowed_reviewed_stack_mana_keys(record, generated_programs, reviewed_programs):
    """Replace the same pinned next-main result during current program assembly."""

    expected, target = stack_mana_value_followup(StackManaValueResult.NEXT_MAIN_COLORLESS)
    reviewed = {program.key: program for program in reviewed_programs}
    return {
        old.key
        for current in generated_programs
        if (old := reviewed.get(current.key)) is not None
        and STACK_MANA_VALUE_FOLLOWUP_CAPABILITY in current.capability_dependencies
        and _same_current_source(record, current, old)
        and (current.event, current.active_zone, current.destination) == ("resolve", "stack", "graveyard")
        and current.ability_id == old.ability_id and current.ability_id.startswith("spell:")
        and current.event_condition is None and old.event_condition is None
        and current.cost_schema is None and old.cost_schema is None
        and not current.requires_arbiter and not current.handlers and not old.handlers
        and thaw_value(current.target_schema) == target
        and tuple(thaw_value(current.effects)) == (expected,)
        and _reviewed_counter_and_colorless_mana(old.effects)
    }


def _same_reviewed_upkeep_payment(effects, cost):
    if len(effects) != 2:
        return False
    counter, delayed = thaw_value(effects)
    if counter != {"op": "counter_stack", "stack": "$target.0"} or not isinstance(delayed, dict):
        return False
    label = delayed.pop("label", None)
    return isinstance(label, str) and bool(label) and delayed == {
        "op": "delayed_pact_payment", "player": "$controller", "cost": cost,
    }


def shadowed_reviewed_pact_counter_keys(record, generated_programs, reviewed_programs):
    """Use the source-equivalent current ordered counter and delayed payment."""

    reviewed = {program.key: program for program in reviewed_programs}
    return {
        old.key
        for current in generated_programs
        if (old := reviewed.get(current.key)) is not None
        and FIXED_NEXT_UPKEEP_PAYMENT_CAPABILITY in current.capability_dependencies
        and _same_current_source(record, current, old)
        and (current.event, current.active_zone, current.destination) == ("resolve", "stack", "graveyard")
        and current.ability_id == old.ability_id and current.ability_id.startswith("spell:")
        and current.event_condition is None and old.event_condition is None
        and current.cost_schema is None and old.cost_schema is None
        and not current.requires_arbiter and not current.handlers and not old.handlers
        and len(current.effects) == 2
        and thaw_value(current.effects[0]) == {"op": "counter_stack_target", "stack": "$target.0"}
        and (cost := fixed_next_upkeep_payment_cost(current.effects[1])) is not None
        and _same_reviewed_upkeep_payment(old.effects, cost)
    }
