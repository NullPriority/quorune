from __future__ import annotations

"""Read-only selected zone-cost forms and declared cardinality validation."""

from typing import Any, Mapping
from ...additional_cost_vocabulary import ZONE_CHANGE_COST_KIND
from ...public_alternative_costs import (
    ANNOUNCED_X_PITCH_POLICY, ANNOUNCED_X_PITCH_PRICE_VERSION, FixedPublicAlternativeCostError,
    FixedPublicAlternativeCostSpec,
)
from ..casting_additional_costs import (
    AdditionalCostError, AdditionalCostQueryHost, FixedZoneChangeAdditionalCost,
    fixed_zone_change_cost_candidates,fixed_zone_change_additional_cost,
)


ANNOUNCED_PITCH_X = "announced_pitch_x"


def _hand_mana_values(host, seat, candidate_refs):
    eligible = set(candidate_refs)
    values = {}
    for object_id in host.state.players[seat].zones["hand"]:
        card = host.state.cards[object_id]
        if card.ref not in eligible:
            continue
        value = host._effective_card_data(card).get("mana_value")
        if type(value) in (int, float) and value >= 0 and value % 1 == 0:
            values[card.ref] = int(value)
    return values


def expand_announced_x_pitch_options(host, seat, source, options, submission, *, hint, has_x):
    from ...semantic_runtime.static_cast_rules import static_cast_prohibition_reason

    expanded = []
    for option in options:
        if option.get("x_value_policy") != ANNOUNCED_X_PITCH_POLICY:
            expanded.append(option)
            continue
        if not has_x:
            continue
        try:
            price = FixedPublicAlternativeCostSpec.from_dict(option["fixed_public_alternative_cost"])
            if price.schema_version != ANNOUNCED_X_PITCH_PRICE_VERSION:
                continue
            cost = fixed_zone_change_additional_cost(price.additional_costs[0])
        except (AdditionalCostError, FixedPublicAlternativeCostError, KeyError, TypeError):
            continue
        candidates = fixed_zone_change_cost_candidates(
            host, actor=seat, cost=cost, exclude_object_id=source.object_id,
        )
        values = set(_hand_mana_values(host, seat, candidates).values())
        if not hint:
            announced = submission.get("x")
            values = {announced} & values if type(announced) is int else set()
        for value in sorted(values):
            if static_cast_prohibition_reason(host, seat, source, x_value=value):
                continue
            expanded.append({
                **option, "id": f"{option['id']}-x{value}",
                "label": f"{option['label']} with X = {value}", ANNOUNCED_PITCH_X: value,
            })
    return expanded


def announced_pitch_candidates(host, seat, option, candidate_refs):
    if option.get("x_value_policy") != ANNOUNCED_X_PITCH_POLICY:
        return tuple(candidate_refs)
    value = option.get(ANNOUNCED_PITCH_X)
    if type(value) is not int or value < 0:
        return ()
    values = _hand_mana_values(host, seat, candidate_refs)
    return tuple(ref for ref in candidate_refs if values.get(ref) == value)


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
    if option.get("x_value_policy") == ANNOUNCED_X_PITCH_POLICY:
        value = option.get(ANNOUNCED_PITCH_X)
        if type(value) is not int or (not hint and response.get("x") != value):
            return False, None
        candidates = list(announced_pitch_candidates(host, seat, option, candidates))
        choice_schema["x"] = {"type": "integer", "minimum": value, "maximum": value, "default": value}
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
    from ..fixed_payment_groups import fixed_color_tap_zone_costs, fixed_life_zone_costs
    from ...nonmana_kicker import distinct_kicker_zone_costs
    tap_zone = fixed_color_tap_zone_costs(costs)
    if (len(costs) != 1 and not fixed_life_zone_costs(costs)
        and not distinct_kicker_zone_costs(option, costs) and not tap_zone):
        return False, None
    if tap_zone:
        from .additional_tap_costs import validate_fixed_tap_payment
        from .source_placement import prospective_fixed_tap_price_host
        try:
            tapped = validate_fixed_tap_payment(host, seat, source, option)
        except (TypeError, ValueError):
            return False, None
        host = prospective_fixed_tap_price_host(host, seat, source.object_id, tapped)
    return _fixed_zone_change_selection(host,seat=seat,source_object_id=source.object_id,cost=cost,
        cost_position=position,response=response,hint=hint,choice_schema=choice_schema,option=option)
