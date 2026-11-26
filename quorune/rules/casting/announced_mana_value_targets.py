"""Price and announce target mana-value limits from the cast's X."""

from __future__ import annotations

from math import ceil

from ..announced_mana_value_target import (
    ANNOUNCED_MANA_VALUE_LIMIT,
    bind_announced_mana_value_limit,
    has_announced_mana_value_limit,
    has_distinct_own_graveyard_mana_value_limit,
)
from ...targets import target_plan
from .spell_text import CAST_TEXT_FRAMES


def bind_announced_target_cost_option(schema, option, response, *, hint, has_mana_x, target_limit=None):
    if not has_announced_mana_value_limit(schema):
        return option
    if not has_mana_x or option.get("target_schema") is not None:
        return None
    announced_x = response.get("x")
    announced_x = 0 if announced_x is None else announced_x
    zero_price = option.get("x_value_policy") == "zero"
    try:
        target = bind_announced_mana_value_limit(schema, 0 if hint else announced_x)
    except (TypeError, ValueError):
        return None
    if hint and not zero_price:
        target.pop("mana_value_max")
        if target.get("distinct_mana_values"):
            if type(target_limit) is not int or target_limit < 0:
                return None
            target["up_to"] = target_limit
    return {**option, "target_schema": target, ANNOUNCED_MANA_VALUE_LIMIT: True}


def _quoted_x_max(option):
    if option.get("x_value_policy") == "zero":
        return 0
    return option.get("choice_schema", {}).get("x", {}).get("maximum")


def _required_x(host, reference):
    snapshot = host._target_snapshot(reference)
    return ceil(snapshot["mana_value"])


def quote_announced_mana_value_target(host, option, schema, *, declared_x=None):
    """Attach the affordable bound and preserve each target-tax witness's own limit."""
    raw = option.to_dict()
    maximum = _quoted_x_max(raw)
    if type(maximum) is not int or maximum < 0:
        return None
    if declared_x is not None:
        if type(declared_x) is not int or not 0 <= declared_x <= maximum:
            return None
        maximum = declared_x
        if "x" in raw.get("choice_schema", {}):
            raw["choice_schema"]["x"].update(minimum=maximum, maximum=maximum, default=maximum)
    bound = bind_announced_mana_value_limit(schema, maximum)
    cap = bound["mana_value_max"]
    distinct_values = has_distinct_own_graveyard_mana_value_limit(schema)
    if CAST_TEXT_FRAMES in raw:
        if distinct_values:
            # This declaration needs a separately assured composed target price.
            return None
        group_id, = raw[CAST_TEXT_FRAMES][0]["groups"].values()
        composed = raw["target_schema"]
        raw["target_schema"] = {
            **composed,
            "groups": [
                {**group, "mana_value_max": cap} if group["id"] == group_id else group
                for group in composed["groups"]
            ],
        }
    else:
        target = raw["target_schema"]
        target["mana_value_max"] = cap
        if distinct_values:
            target["up_to"] = min(target["up_to"], bound["up_to"])
        group_id = target_plan(target).groups[0].group_id
    raw["x_bound_group"] = group_id
    witness = raw.get("target_cost_witness")
    if witness is not None:
        references = witness["targets"][group_id]
        if not distinct_values and len(references) != 1:
            return None
        if any(_required_x(host, ref) > maximum for ref in references):
            return None
        raw["target_x_maxima"] = dict.fromkeys(references, maximum)
    return type(option).from_dict(raw)


def with_announced_target_ranges(host, public_schema, option):
    """Expose the joint target/X bounds used by this quote."""
    if not option.get(ANNOUNCED_MANA_VALUE_LIMIT):
        return public_schema
    maximum = _quoted_x_max(option)
    target_maxima = option.get("target_x_maxima", {})
    group_id = option["x_bound_group"]
    target = next(group for group in public_schema["groups"] if group["id"] == group_id)
    return {**public_schema, "x_bound_group": group_id, "x_bounds_by_ref": {
        ref: {"minimum": _required_x(host, ref), "maximum": target_maxima.get(ref, maximum)}
        for ref in target["legal_refs"]
    }}
