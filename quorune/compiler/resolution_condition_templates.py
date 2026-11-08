from __future__ import annotations

"""One public-state conditional result, optionally after a mandatory prefix."""

from copy import deepcopy
import re
from typing import Callable

from ..resolution_conditions import (
    RESOLUTION_ALTERNATIVE_SCHEMA_VERSION,
    RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION,
    resolution_condition_is_closed,
    resolution_result_is_fixed,
    validate_resolution_condition_instruction,
)
from .closed_effect_programs import CompiledEffectTemplate, _top_level_positions
from .public_state_queries import fixed_public_state_condition


_LINKED_BRANCH_WORDS = re.compile(r"\b(?:instead|this way|that much|otherwise|if)\b", re.I)
_COMPOUND_SUFFIX_RESULT = re.compile(r"\bthen\b|;", re.I)
_OTHERWISE = ". Otherwise, "


def _leading_condition_parts(text: str) -> tuple[tuple[str, str, str], ...]:
    if "If " not in text and "Then if " not in text:
        return ()
    positions = _top_level_positions(text, "If ")
    separator = "If "
    if positions == () and "Then if " in text:
        separator = "Then if "
        positions = _top_level_positions(text, separator)
    if positions is None or len(positions) != 1 or not text.endswith("."):
        return ()
    position = positions[0]
    prefix = text[:position].strip()
    if (prefix and not prefix.endswith(".")) or (separator == "Then if " and not prefix):
        return ()
    body = text[position + len(separator):]
    if _LINKED_BRANCH_WORDS.search(body):
        return ()
    commas = _top_level_positions(body, ", ")
    return tuple((prefix, body[:comma], body[comma + 2:]) for comma in commas or ())


def _suffix_condition_parts(text: str) -> tuple[tuple[str, str, str], ...]:
    """Read a condition following the instruction it guards."""
    positions = _top_level_positions(text, " if ")
    if positions is None or len(positions) != 1 or not text.endswith("."):
        return ()
    split = positions[0]
    before, condition = text[:split], text[split + len(" if "):-1]
    stops = _top_level_positions(before, ". ")
    if stops is None:
        return ()
    prefix = before[:stops[-1] + 1] if stops else ""
    result = before[stops[-1] + 2:] if stops else before
    if prefix:
        result = result.removeprefix("Then ")
    return ((prefix, condition, result + "."),) if result and condition else ()


def resolution_condition_template(
    text: str,
    *,
    source_name: str,
    compile_component: Callable[[str], CompiledEffectTemplate],
) -> CompiledEffectTemplate | None:
    normalized = re.sub(r"^[A-Z][A-Za-z' ]{0,80} — ", "", text.strip())
    from .public_self_replacement_templates import fixed_public_self_replacement_template
    replacement = fixed_public_self_replacement_template(
        normalized, source_name=source_name, compile_component=compile_component,
    )
    if replacement is not None:
        return replacement
    alternative = _top_level_positions(normalized, _OTHERWISE) if _OTHERWISE in normalized else ()
    if alternative is None or len(alternative) > 1:
        return None
    otherwise = None
    if alternative:
        split = alternative[0]
        otherwise = normalized[split + len(_OTHERWISE):]
        normalized = normalized[:split + 1]
        if not otherwise.endswith(".") or _LINKED_BRANCH_WORDS.search(otherwise):
            return None
    parts = _leading_condition_parts(normalized)
    standalone_suffix = not parts and otherwise is None
    if not parts:
        parts = _suffix_condition_parts(normalized)
    candidates = []
    for prefix, condition_text, body in parts:
        if _LINKED_BRANCH_WORDS.search(body):
            continue
        if standalone_suffix and _COMPOUND_SUFFIX_RESULT.search(body):
            continue
        condition = fixed_public_state_condition(condition_text, source_name=source_name)
        if condition is None or not resolution_condition_is_closed(condition):
            continue
        conditional = compile_component(body)
        mandatory = compile_component(prefix) if prefix else (None, (), None, ())
        if conditional[0] is None or not conditional[1] or conditional[2] is not None or (prefix and mandatory[0] is None):
            continue
        if standalone_suffix and len(conditional[1]) != 1:
            continue
        if not resolution_result_is_fixed(conditional[1], conditional[3]):
            continue
        if "fixed-next-turn-upkeep-draw" in (*mandatory[3], *conditional[3]):
            continue
        schemas = [schema for _, _, schema, _ in (mandatory, conditional) if schema is not None]
        # Independent targets in both branches need a different binding owner.
        if len(schemas) > 1 or len(mandatory[1]) + len(conditional[1]) > 8:
            continue
        wrapper = {
            "op": RESOLUTION_CONDITION_OPERATION,
            "player": "$controller",
            "condition": condition.to_dict(),
            "effects": deepcopy(list(conditional[1])),
            "mechanic_ids": list(conditional[3]),
            "prefix_mechanic_ids": list(mandatory[3]),
        }
        template_id = "fixed-resolution-public-condition-v1"
        mechanics = (*mandatory[3], *conditional[3])
        if otherwise is not None:
            alternate = compile_component(otherwise)
            if (alternate[0] is None or alternate[2] is not None or len(mandatory[1]) > 1):
                continue
            wrapper.update(
                schema_version=RESOLUTION_ALTERNATIVE_SCHEMA_VERSION,
                otherwise_effects=deepcopy(list(alternate[1])),
                otherwise_mechanic_ids=list(alternate[3]),
            )
            try:
                validate_resolution_condition_instruction(wrapper)
            except ValueError:
                continue
            mechanics += alternate[3]
            template_id = "fixed-resolution-public-alternative-v1"
        candidates.append((
            template_id,
            (*deepcopy(mandatory[1]), wrapper),
            deepcopy(schemas[0]) if schemas else None,
            tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, *mechanics))),
        ))
    return candidates[0] if len(candidates) == 1 else None
