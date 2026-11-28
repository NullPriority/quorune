from __future__ import annotations

"""Bind declared target counts before the existing cast target/cost queries."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ..announced_x_modal import DESTROY_MODE, bind_announced_x_modal_targets, uses_announced_x_modal
from ...target_cardinality import (
    AnnouncedTargetCardinality, bind_announced_target_schema, link_announced_public_bounds,
)


def bind_target_count_option(program: Any, option: Mapping[str, Any], response: Mapping[str, Any],
    *, has_mana_x: bool, has_declared_cost_x: bool=False, target_limit: int | None=None) -> dict[str, Any] | None:
    schema = getattr(program, "target_schema", None)
    from .kicked_targets import bind_kicked_targets
    try:option=bind_kicked_targets(program,option)
    except (TypeError,ValueError):return None
    if uses_announced_x_modal(schema):
        if not has_mana_x or option.get("target_schema") is not None:
            return None
        try:
            bound = bind_announced_x_modal_targets(schema, response.get("x"))
        except (TypeError, ValueError, KeyError):
            return None
        return {**deepcopy(dict(option)), "target_schema": bound, "announced_x_modal_mode": DESTROY_MODE}
    if not isinstance(schema, Mapping) or "count_parameter" not in schema:
        return deepcopy(dict(option))
    try:
        spec = AnnouncedTargetCardinality.from_dict(schema["count_parameter"])
        if option.get("target_schema") is not None:
            return None
        if spec.parameter != "any_number" and not (has_mana_x or has_declared_cost_x):
            return None
        value = target_limit if spec.parameter == "any_number" else response.get("x")
        if spec.parameter == "any_number" and value is None:
            return None
        _minimum, maximum = spec.bounds(value)
        bound = bind_announced_target_schema(schema, value)
    except (TypeError, ValueError):
        return None
    return {**deepcopy(dict(option)), "target_schema": bound,
        **({"announced_target_x": maximum} if spec.parameter == "x" else {}),
        "target_count_parameter": spec.to_dict()}


def public_target_count_schema(schema: Mapping[str, Any], option: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(schema))
    mode = option.get("announced_x_modal_mode")
    if mode == DESTROY_MODE:
        incoming = result.get("mode_schemas", {}).get(mode)
        if incoming is not None:
            result["mode_schemas"][mode] = link_announced_public_bounds(
                incoming, AnnouncedTargetCardinality().to_dict(),
            )
        return result
    raw = option.get("target_count_parameter")
    if raw is None:
        return result
    result = link_announced_public_bounds(result, raw)
    group_id = _spliced_announced_phase_group(option)
    if group_id is not None:
        result["x_bound_group"] = group_id
        for group in result["groups"]:
            if group["id"] != group_id:
                group.pop("maximum_from", None)
                group.pop("count_from", None)
    return result


def _spliced_announced_phase_group(option):
    from ...phasing_model import has_announced_phase_out_targets
    from .spell_text import CAST_TEXT_FRAMES

    if CAST_TEXT_FRAMES not in option or not has_announced_phase_out_targets(option.get("effects", ())):
        return None
    group_id, = option[CAST_TEXT_FRAMES][0]["groups"].values()
    return group_id


def target_count_cost_preview(host, seat, card, program, option):
    """Expose minimal tax witnesses without substituting zero for every X.

    Target tax ownership still selects and prices each source combination. A
    temporary up-to schema permits the minimal witness for each X combination;
    minimum-one schemas retain that positive minimum. Ordinary execution uses
    the declaration's bound schema.
    """
    if "target_count_parameter" not in option or option.get("x_value_policy") == "zero":
        return option
    from ...target_source import spell_targeting_source
    group_id = _spliced_announced_phase_group(option)
    schema = dict(option["target_schema"])
    group = schema
    if group_id is not None:
        schema = deepcopy(schema)
        group = next(group for group in schema["groups"] if group["id"] == group_id)
    spec = AnnouncedTargetCardinality.from_dict(option["target_count_parameter"])
    for field in ("count", "up_to", "min", "max"):
        group.pop(field, None)
    bound_field = "max" if spec.selection == "one_or_more" else "up_to"
    if spec.selection == "one_or_more":
        group["min"] = 1
    group[bound_field] = len(host.state.cards)
    public = host._public_target_schema(seat, schema,
        acting_source=spell_targeting_source(host, card), source_ref=card.ref, announcing_targets=True)
    if public is None:
        return option
    view = next(group for group in public["groups"] if group["id"] == group_id) if group_id is not None else public
    maximum = len(view.get("legal_refs", ()))
    group[bound_field] = maximum
    from .targeted_costs import targeted_spell_cost_sources
    return {**option, **({"target_schema": schema} if targeted_spell_cost_sources(host, seat,card=card,spell_program=program) else {}),
        "target_count_preview_limit": maximum}


def target_count_preview_maximum(option, maximum):
    """An exact-X tax witness also needs mana for its chosen target count."""
    if "target_count_preview_limit" not in option:
        return maximum
    spec = AnnouncedTargetCardinality.from_dict(option["target_count_parameter"])
    if spec.parameter == "any_number":
        return maximum
    witness = option.get("target_cost_witness", {}).get("targets", {})
    count = sum(len(refs) for refs in witness.values())
    if count > maximum:
        return -1
    return maximum if spec.selection == "up_to" else min(maximum, option["target_count_preview_limit"])
