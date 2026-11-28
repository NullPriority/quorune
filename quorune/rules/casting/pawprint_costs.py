"""Bind counted Pawprint selections to ordinary cast options and target groups."""

from collections import Counter
from copy import deepcopy

from ...pawprint_modes import (
    PAWPRINT_CAPABILITY, PAWPRINT_FIELD, PAWPRINT_RESULT_MODES, PawprintModeBudget,
    REPEATABLE_MODE_CAPABILITY, REPEATABLE_MODE_FIELD, REPEATABLE_MODE_RESULT, FixedRepeatableModeChoices,
)


def with_pawprint_mode_cost_options(program, schema, options, response, *, hint):
    if PAWPRINT_FIELD in schema and REPEATABLE_MODE_FIELD in schema:
        return []
    if REPEATABLE_MODE_FIELD in schema:
        field, capability, model = REPEATABLE_MODE_FIELD, REPEATABLE_MODE_CAPABILITY, FixedRepeatableModeChoices
        result_modes, suffix, base_option = REPEATABLE_MODE_RESULT, "repeatable_modes", "repeatable_mode_base_option_id"
    elif PAWPRINT_FIELD in schema:
        field, capability, model = PAWPRINT_FIELD, PAWPRINT_CAPABILITY, PawprintModeBudget
        result_modes, suffix, base_option = PAWPRINT_RESULT_MODES, "pawprint", "pawprint_base_option_id"
    else:
        return options
    try:
        if (program is None or capability not in program.capability_dependencies
                or schema[field] != (program.cost_schema or {}).get(field)):
            return []
        budget = model.from_dict(schema[field])
        budget.validate_schema(program.target_schema)
        submitted = response.get("modes", ())
        choices = budget.available_choices() if hint and "modes" not in response else (submitted,)
        selections = tuple(budget.selection(choice) for choice in choices)
    except (TypeError, ValueError):
        return []
    expanded = []
    for option in options:
        for instances in selections:
            bound = deepcopy(option)
            modes = [alias for alias, _ in instances]
            bound["id"] = option["id"] + "+" + suffix + ":" + (",".join(modes) or "empty")
            bound[base_option] = option["id"]
            counts = Counter(mode for _, mode in instances)
            summary = " + ".join(
                f"Mode {mode[-1]}" + (f" ×{count}" if count > 1 else "")
                for mode, count in counts.items()
            ) or "No modes"
            label = option.get("label")
            bound["label"] = f"{label} ({summary})" if label else summary
            bound[result_modes] = modes
            bound["target_schema"] = budget.selected_schema(program.target_schema, instances)
            expanded.append(bound)
    return expanded
