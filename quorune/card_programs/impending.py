"""Exact CardProgram scopes for the paid Impending lifetime."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE, FixedPublicStateFact
from ..impending_model import IMPENDING_CAPABILITY, IMPENDING_COST_EVENT, IMPENDING_HANDLER_ID
from ..replacement.immutable import thaw_value
from ..rules.impending import impending_end_step_condition, impending_end_step_effect, impending_time_counter_condition


def _uses_impending_condition(condition) -> bool:
    if not isinstance(condition, Mapping):
        return False
    if condition.get("fact") == FixedPublicStateFact.SOURCE_PAID_IMPENDING_WITH_TIME_COUNTER.value:
        return True
    if _uses_impending_condition(condition.get("condition")):
        return True
    for key in ("all", "any"):
        values = condition.get(key, ())
        if isinstance(values, (list, tuple)) and any(_uses_impending_condition(value) for value in values):
            return True
    return _uses_impending_condition(condition.get("not"))


def impending_program_dependencies(program) -> tuple[str, ...]:
    condition = program.event_condition
    handlers = tuple(program.handlers)
    paid_trigger = _uses_impending_condition(condition)
    marked = tuple(handler for handler in handlers if handler.get("handler_id") == IMPENDING_HANDLER_ID
                   or _uses_impending_condition(handler.get("source_condition")))
    if not paid_trigger and not marked:
        return ()
    if program.target_schema is not None:
        raise ValueError("Impending cannot bind a targeting scope")
    if paid_trigger:
        if (
            handlers or program.event != "step.begin" or program.active_zone != "battlefield"
            or thaw_value(condition) != impending_end_step_condition()
            or tuple(thaw_value(effect) for effect in program.effects) != (impending_end_step_effect(),)
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition",
                    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE}.issubset(program.coverage)
        ):
            raise ValueError("Impending requires the current controller's exact intervening end-step trigger")
        return (IMPENDING_CAPABILITY, "counter.removal.fixed_effect",
                "counter.removal.fixed_source_effect", "trigger.placement.apnap")
    if len(handlers) != 1 or len(marked) != 1 or program.effects or condition is not None:
        raise ValueError("Impending requires a single static price or type descriptor")
    handler = marked[0]
    if handler.get("handler_id") == IMPENDING_HANDLER_ID:
        if program.event != IMPENDING_COST_EVENT or program.active_zone != "stack":
            raise ValueError("Impending price belongs to the casting-cost scope")
        return (IMPENDING_CAPABILITY,)
    from ..semantic_runtime.fixed_characteristic_settings import (
        CREATURE_TYPE_REMOVAL_CAPABILITY, CREATURE_TYPE_REMOVAL_HANDLER,
    )

    if (
        handler.get("handler_id") != CREATURE_TYPE_REMOVAL_HANDLER
        or program.event != "characteristics.evaluate" or program.active_zone != "battlefield"
        or thaw_value(handler.get("source_condition")) != impending_time_counter_condition().to_dict()
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Impending requires the paid source's current creature-type condition")
    return (IMPENDING_CAPABILITY, CREATURE_TYPE_REMOVAL_CAPABILITY)
