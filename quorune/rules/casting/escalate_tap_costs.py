"""Price a mode selection through the ordinary fixed additional tap owner."""

from copy import deepcopy
from itertools import combinations

from ...escalate_tap_model import ESCALATE_TAP_CAPABILITY, ESCALATE_TAP_FIELD, EscalateTapModeSpec
from ..modal_selection import canonical_modes
from ..tap_additional_cost import FixedTapAdditionalCost


def with_escalate_tap_mode_cost_options(program, schema, options, response, *, hint):
    if ESCALATE_TAP_FIELD not in schema:
        return options
    try:
        if (program is None or ESCALATE_TAP_CAPABILITY not in program.capability_dependencies
                or schema[ESCALATE_TAP_FIELD] != (program.cost_schema or {}).get(ESCALATE_TAP_FIELD)):
            return []
        policy = EscalateTapModeSpec.from_dict(schema[ESCALATE_TAP_FIELD])
        policy.validate_schema(program.target_schema)
        if response.get("modes"):
            selections = (canonical_modes(program.target_schema, response["modes"]),)
        elif hint:
            selections = tuple(choice for count in range(1, len(policy.mode_ids) + 1)
                               for choice in combinations(policy.mode_ids, count))
        else:
            return []
        choices = tuple((selected, policy.tap_count(selected)) for selected in selections)
    except (TypeError, ValueError):
        return []
    expanded = []
    for option in options:
        for selected, tap_count in choices:
            if not tap_count and not hint and response.get("tap_cost_cards"):
                continue
            bound = deepcopy(option)
            bound["id"] = option["id"] + "+escalate:" + ",".join(selected)
            bound["escalate_base_option_id"] = option["id"]
            bound["label"] = str(option.get("label") or "Cast") + " with " + ", ".join(
                mode.replace("_", " ") for mode in selected
            )
            bound["target_schema"] = {
                "mode_count": len(selected), "min_modes": len(selected), "max_modes": len(selected),
                "modes": {mode: deepcopy(program.target_schema["modes"][mode]) for mode in selected},
            }
            if tap_count:
                cost = FixedTapAdditionalCost(tap_count, ("creature",)).to_descriptor()
                bound["_additional_option_costs"] = [*bound.get("_additional_option_costs", ()), cost]
            expanded.append(bound)
    return expanded
