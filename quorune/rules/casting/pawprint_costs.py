"""Bind counted Pawprint selections to ordinary cast options and target groups."""

from collections import Counter
from copy import deepcopy

from ...pawprint_modes import PAWPRINT_CAPABILITY, PAWPRINT_FIELD, PAWPRINT_RESULT_MODES, PawprintModeBudget


def with_pawprint_mode_cost_options(program, schema, options, response, *, hint):
    if PAWPRINT_FIELD not in schema:
        return options
    try:
        if (program is None or PAWPRINT_CAPABILITY not in program.capability_dependencies
                or schema[PAWPRINT_FIELD] != (program.cost_schema or {}).get(PAWPRINT_FIELD)):
            return []
        budget = PawprintModeBudget.from_dict(schema[PAWPRINT_FIELD])
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
            bound["id"] = option["id"] + "+pawprint:" + (",".join(modes) or "empty")
            bound["pawprint_base_option_id"] = option["id"]
            counts = Counter(mode for _, mode in instances)
            summary = " + ".join(
                f"Mode {mode[-1]}" + (f" ×{count}" if count > 1 else "")
                for mode, count in counts.items()
            ) or "No modes"
            label = option.get("label")
            bound["label"] = f"{label} ({summary})" if label else summary
            bound[PAWPRINT_RESULT_MODES] = modes
            bound["target_schema"] = budget.selected_schema(program.target_schema, instances)
            expanded.append(bound)
    return expanded
