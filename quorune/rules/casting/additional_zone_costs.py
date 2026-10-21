from __future__ import annotations

"""Read-only selected zone-cost forms and declared cardinality validation."""

from typing import Any, Mapping
from ...additional_cost_vocabulary import ZONE_CHANGE_COST_KIND
from ..casting_additional_costs import (
    AdditionalCostError, AdditionalCostQueryHost, FixedZoneChangeAdditionalCost,
    fixed_zone_change_cost_candidates,fixed_zone_change_additional_cost,
)


def _fixed_zone_change_selection(
    host: AdditionalCostQueryHost,
    *,
    seat: str,
    source_object_id: str,
    cost: FixedZoneChangeAdditionalCost,
    cost_position: int,
    response: Mapping[str, Any],
    hint: bool,
    choice_schema: dict[str, Any],
    option: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    candidates = list(
        fixed_zone_change_cost_candidates(
            host,
            actor=seat,
            cost=cost,
            exclude_object_id=source_object_id,
        )
    )
    try:
        count = cost.required_count(response.get('x'))
    except AdditionalCostError:
        return False, None
    if cost.count_parameter is not None:
        maximum = min(len(candidates), int(choice_schema.get('x', {}).get('maximum', len(candidates))))
        if option.get('x_value_policy') == 'zero':
            maximum = 0
        from .target_cardinality import target_count_preview_maximum
        maximum=target_count_preview_maximum(option,maximum)
        if count > maximum:
            return False, None
        choice_schema['x'] = {'type': 'integer', 'minimum': 0, 'maximum': maximum}
        option['announced_additional_x'] = count
    if len(candidates) < count:
        return False, None
    choice_schema[cost.choice_field] = {
        "type": "object_ref_array",
        "count": count,
        "legal_refs": candidates,
        "zone": cost.origin_zone,
        "destination": cost.destination_zone,
        "payment": cost.log_kind,
    }
    if cost.count_parameter is not None:
        choice_schema[cost.choice_field]['count_from'] = 'x'
    if hint:
        return True, None
    raw_values = response.get(cost.choice_field)
    if raw_values is None:
        raw_values = response.get("cost_cards", [] if count == 0 else None)
    if not isinstance(raw_values, (list, tuple)):
        return False, None
    values = list(raw_values)
    if (
        len(values) != count
        or len(set(values)) != count
        or any(type(value) is not str or value not in candidates for value in values)
    ):
        return False, None
    selected = {
        "kind": ZONE_CHANGE_COST_KIND,
        "operation": cost.operation,
        "cost_position": cost_position,
    }
    selected["card" if count == 1 else "cards"] = (
        values[0] if count == 1 else values
    )
    return True, selected


def select_zone_change_additional_cost(host,seat,source,option,costs,response,*,position,hint,choice_schema):
    """Select a closed price without paying it or choosing a random subset."""
    from ..random_discard_additional_cost import RANDOM_SPELL_COST_KIND,select_random_spell_cost
    if costs[position].get('kind')==RANDOM_SPELL_COST_KIND:
        return select_random_spell_cost(host,seat,source,costs,response,position=position,choice_schema=choice_schema)
    try:cost=fixed_zone_change_additional_cost(costs[position])
    except AdditionalCostError:return False,None
    if cost is None:return None
    if len(costs)!=1:return False,None
    return _fixed_zone_change_selection(host,seat=seat,source_object_id=source.object_id,cost=cost,
        cost_position=position,response=response,hint=hint,choice_schema=choice_schema,option=option)
