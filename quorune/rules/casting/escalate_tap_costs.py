"""Bind Escalate mode offers to the ordinary mana and additional tap owners."""

from copy import deepcopy
from itertools import combinations

from ...escalate_mana_model import ESCALATE_MANA_CAPABILITY, ESCALATE_MANA_FIELD, EscalateManaModeSpec
from ...escalate_tap_model import ESCALATE_TAP_CAPABILITY, ESCALATE_TAP_FIELD, EscalateTapModeSpec
from ..modal_selection import canonical_modes
from ..tap_additional_cost import FixedTapAdditionalCost


def _escalate_selections(program, policy, response, *, hint):
    policy.validate_schema(program.target_schema)
    if response.get("modes"):
        return (canonical_modes(program.target_schema, response["modes"]),)
    if hint:
        return tuple(
            selected for count in range(1, len(policy.mode_ids) + 1)
            for selected in combinations(policy.mode_ids, count)
        )
    return ()


def _escalate_quote(option, selected, schema):
    bound = deepcopy(option)
    bound["id"] = option["id"] + "+escalate:" + ",".join(selected)
    bound["escalate_base_option_id"] = option["id"]
    bound["label"] = str(option.get("label") or "Cast") + " with " + ", ".join(
        mode.replace("_", " ") for mode in selected
    )
    bound["target_schema"] = {
        "mode_count": len(selected), "min_modes": len(selected), "max_modes": len(selected),
        "modes": {mode: deepcopy(schema["modes"][mode]) for mode in selected},
    }
    return bound


def with_escalate_tap_mode_cost_options(program, schema, options, response, *, hint):
    if ESCALATE_TAP_FIELD not in schema:
        return options
    try:
        if (program is None or ESCALATE_TAP_CAPABILITY not in program.capability_dependencies
                or schema[ESCALATE_TAP_FIELD] != (program.cost_schema or {}).get(ESCALATE_TAP_FIELD)):
            return []
        policy = EscalateTapModeSpec.from_dict(schema[ESCALATE_TAP_FIELD])
        selections = _escalate_selections(program, policy, response, hint=hint)
        choices = tuple((selected, policy.tap_count(selected)) for selected in selections)
    except (TypeError, ValueError):
        return []
    expanded = []
    for option in options:
        for selected, tap_count in choices:
            if not tap_count and not hint and response.get("tap_cost_cards"):
                continue
            bound = _escalate_quote(option, selected, program.target_schema)
            if tap_count:
                cost = FixedTapAdditionalCost(tap_count, ("creature",)).to_descriptor()
                bound["_additional_option_costs"] = [*bound.get("_additional_option_costs", ()), cost]
            expanded.append(bound)
    return expanded


def with_escalate_mana_mode_cost_options(program, schema, options, response, *, hint):
    if ESCALATE_MANA_FIELD not in schema:
        return options
    try:
        if (
            program is None or ESCALATE_MANA_CAPABILITY not in program.capability_dependencies
            or set(program.cost_schema or {}) != {ESCALATE_MANA_FIELD}
            or schema[ESCALATE_MANA_FIELD] != program.cost_schema[ESCALATE_MANA_FIELD]
        ):
            return []
        policy = EscalateManaModeSpec.from_dict(schema[ESCALATE_MANA_FIELD])
        selections = _escalate_selections(program, policy, response, hint=hint)
        choices = tuple((selected, policy.additional_mana(selected)) for selected in selections)
    except (TypeError, ValueError):
        return []
    expanded = []
    for option in options:
        for selected, extra in choices:
            bound = _escalate_quote(option, selected, program.target_schema)
            for symbol, count in extra.items():
                bound["requirements"][symbol] += count
            expanded.append(bound)
    return expanded


def with_escalate_mode_cost_options(program, schema, options, response, *, hint):
    if ESCALATE_MANA_FIELD in schema:
        return with_escalate_mana_mode_cost_options(program, schema, options, response, hint=hint)
    return with_escalate_tap_mode_cost_options(program, schema, options, response, hint=hint)
