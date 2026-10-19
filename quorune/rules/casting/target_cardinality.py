from __future__ import annotations

"""Bind declared target counts before the existing cast target/cost queries."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ...target_cardinality import (
    AnnouncedTargetCardinality, bind_announced_target_schema, link_announced_public_bounds,
)


def bind_target_count_option(program: Any, option: Mapping[str, Any], response: Mapping[str, Any],
    *, has_mana_x: bool) -> dict[str, Any] | None:
    schema = getattr(program, "target_schema", None)
    if not isinstance(schema, Mapping) or "count_parameter" not in schema:
        return deepcopy(dict(option))
    if not has_mana_x or option.get("target_schema") is not None:
        return None
    try:
        spec = AnnouncedTargetCardinality.from_dict(schema["count_parameter"])
        _minimum, maximum = spec.bounds(response.get("x"))
        bound = bind_announced_target_schema(schema, response.get("x"))
    except (TypeError, ValueError):
        return None
    return {**deepcopy(dict(option)), "target_schema": bound,
        "announced_target_x": maximum, "target_count_parameter": spec.to_dict()}


def public_target_count_schema(schema: Mapping[str, Any], option: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(schema))
    raw = option.get("target_count_parameter")
    if raw is None:
        return result
    return link_announced_public_bounds(result, raw)


def target_count_cost_preview(host, seat, card, program, option):
    """Expose minimal tax witnesses without substituting zero for every X.

    Target tax ownership still selects and prices each source combination. A
    temporary up-to schema permits the minimal witness for each combination;
    ordinary execution always uses the exact schema bound to the submitted X.
    """
    if "target_count_parameter" not in option or option.get("x_value_policy") == "zero":
        return option
    from ...target_source import spell_targeting_source
    schema = dict(option["target_schema"])
    schema.pop("count", None); schema.pop("up_to", None)
    schema["up_to"] = len(host.state.cards)
    public = host._public_target_schema(seat, schema,
        acting_source=spell_targeting_source(host, card), source_ref=card.ref)
    if public is None:
        return option
    schema["up_to"] = len(public.get("legal_refs", ()))
    from .targeted_costs import targeted_spell_cost_sources
    return {**option, **({"target_schema": schema} if targeted_spell_cost_sources(host, seat) else {}),
        "target_count_preview_limit": len(public.get("legal_refs", ()))}


def target_count_preview_maximum(option, maximum):
    """An exact-X tax witness also needs mana for its chosen target count."""
    if "target_count_preview_limit" not in option:
        return maximum
    spec = AnnouncedTargetCardinality.from_dict(option["target_count_parameter"])
    witness = option.get("target_cost_witness", {}).get("targets", {})
    count = sum(len(refs) for refs in witness.values())
    if count > maximum:
        return -1
    return maximum if spec.selection == "up_to" else min(maximum, option["target_count_preview_limit"])
