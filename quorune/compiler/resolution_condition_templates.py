from __future__ import annotations

"""One public-state conditional result, optionally after a mandatory prefix."""

from copy import deepcopy
import re
from typing import Any, Callable, Mapping

from ..resolution_conditions import (
    RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION,
    resolution_condition_is_closed,
    resolution_result_is_fixed,
)
from .closed_effect_programs import CompiledEffectTemplate, _top_level_positions
from .public_state_queries import fixed_public_state_condition


def resolution_condition_template(
    text: str,
    *,
    source_name: str,
    compile_component: Callable[[str], CompiledEffectTemplate],
) -> CompiledEffectTemplate | None:
    normalized = re.sub(r"^[A-Z][A-Za-z' ]{0,80} — ", "", text.strip())
    positions = _top_level_positions(normalized, "If ")
    if positions is None or len(positions) != 1 or not normalized.endswith("."):
        return None
    position = positions[0]
    prefix = normalized[:position].strip()
    if prefix and not prefix.endswith("."):
        return None
    body = normalized[position + 3 :]
    if re.search(r"\b(?:instead|this way|that much|otherwise|if)\b", body, re.IGNORECASE):
        return None
    commas = _top_level_positions(body, ", ")
    if commas is None:
        return None
    candidates = []
    for comma in commas:
        condition = fixed_public_state_condition(body[:comma], source_name=source_name)
        if condition is None or not resolution_condition_is_closed(condition):
            continue
        conditional = compile_component(body[comma + 2 :])
        mandatory = compile_component(prefix) if prefix else (None, (), None, ())
        if conditional[0] is None or not conditional[1] or conditional[2] is not None or (prefix and mandatory[0] is None):
            continue
        if not resolution_result_is_fixed(conditional[1], conditional[3]):
            continue
        if "fixed-next-turn-upkeep-draw" in (*mandatory[3], *conditional[3]):
            continue
        schemas = [schema for _, _, schema, _ in (mandatory, conditional) if schema is not None]
        # Independent targets in both branches need a different binding owner.
        if len(schemas) > 1 or len(mandatory[1]) + len(conditional[1]) > 8:
            continue
        candidates.append((
            "fixed-resolution-public-condition-v1",
            (*deepcopy(mandatory[1]), {
                "op": RESOLUTION_CONDITION_OPERATION,
                "player": "$controller",
                "condition": condition.to_dict(),
                "effects": deepcopy(list(conditional[1])),
                "mechanic_ids": list(conditional[3]),
                "prefix_mechanic_ids": list(mandatory[3]),
            }),
            deepcopy(schemas[0]) if schemas else None,
            tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, *mandatory[3], *conditional[3]))),
        ))
    return candidates[0] if len(candidates) == 1 else None
