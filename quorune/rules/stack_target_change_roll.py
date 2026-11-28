from __future__ import annotations

"""Resolve one power-adjusted d20 before the ordinary stack target choices."""

from ..public_characteristic_aggregate_model import CONTROLLED_CREATURES_POWER_QUERY, PublicCharacteristicAggregateSpec
from ..public_characteristic_aggregates import current_public_characteristic_aggregate
from ..stack_target_change_model import (
    COPY_AFTER_RETARGET,
    POWER_ADJUSTED_RETARGET_ROLL,
    OptionalStackTargetChangeSpec,
    matches_announced_stack_target,
    power_adjusted_retarget_roll,
)
from .random_selection import roll_fixed_die


_GREATEST_POWER = PublicCharacteristicAggregateSpec(
    "power", "maximum", "source_controller", CONTROLLED_CREATURES_POWER_QUERY,
)


def bind_power_adjusted_retarget_roll(host, item, effect, *, instruction_pointer):
    if (type(effect.get(POWER_ADJUSTED_RETARGET_ROLL)) is not int
            or dict(effect) != power_adjusted_retarget_roll()
            or item.kind not in {"spell", "spell_copy"}
            or item.context.get("currently_resolving") is not True
            or len(item.targets) != 1):
        raise ValueError("Power-adjusted target selection requires its exact resolving spell")
    target = next((row for row in host.state.stack if row.ref == item.targets[0] and row.ref != item.ref), None)
    if target is None or not matches_announced_stack_target(target):
        return None
    controller = item.controller
    modifier = current_public_characteristic_aggregate(host, controller, _GREATEST_POWER)
    natural = roll_fixed_die(
        20, seed=host.state.config.seed, instruction_key=f"{item.ref}|{instruction_pointer}", seat=controller,
    )
    result = max(0, natural + modifier)
    host._log(
        controller, "die.roll", f"{controller} rolled {natural} on a d20 with a modifier of {modifier}.",
        {"player": controller, "sides": 20, "natural_result": natural, "modifier": modifier,
         "result": result, "stack": item.ref}, importance=2,
    )
    if not result:
        return None
    change, = OptionalStackTargetChangeSpec(True).effects
    change["stack"] = target.ref
    if result >= 15:
        change[COPY_AFTER_RETARGET] = True
    return change
