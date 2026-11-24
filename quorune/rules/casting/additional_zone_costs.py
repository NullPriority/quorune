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
    allow_generic_refs: bool = True,
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
    if raw_values is None and allow_generic_refs:
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
    from ..fixed_payment_groups import fixed_color_tap_zone_costs, fixed_discard_sacrifice_order, fixed_life_zone_costs
    from ..fixed_payment_groups import FIXED_DISCARD_SACRIFICE_FIELD, WHOLE_DISCARD_SACRIFICE_VERSION
    if option.get(FIXED_DISCARD_SACRIFICE_FIELD) == WHOLE_DISCARD_SACRIFICE_VERSION:
        from .whole_zone_cost_frame import WHOLE_ZONE_SELECTION_FIELDS
        return not WHOLE_ZONE_SELECTION_FIELDS.intersection(response), None
    from ...nonmana_kicker import distinct_kicker_zone_costs
    tap_zone = fixed_color_tap_zone_costs(costs)
    try: ordered_pair = fixed_discard_sacrifice_order(option, costs)
    except ValueError: return False, None
    if (len(costs) != 1 and not fixed_life_zone_costs(costs)
        and not distinct_kicker_zone_costs(option, costs) and not tap_zone and not ordered_pair):
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
        cost_position=position,response=response,hint=hint,choice_schema=choice_schema,option=option,
        allow_generic_refs=not ordered_pair)


def expand_discard_sacrifice_orders(options, cost_schema):
    from ..fixed_payment_groups import (
        FIXED_DISCARD_SACRIFICE_FIELD, FIXED_DISCARD_SACRIFICE_ORDER, WHOLE_DISCARD_SACRIFICE_VERSION,
        fixed_discard_sacrifice_costs, fixed_discard_sacrifice_dependencies,
    )

    if not fixed_discard_sacrifice_dependencies(cost_schema):
        return options
    costs = fixed_discard_sacrifice_costs(cost_schema["additional_costs"])
    expanded = []
    version = cost_schema[FIXED_DISCARD_SACRIFICE_FIELD]
    marker = {FIXED_DISCARD_SACRIFICE_FIELD: version} if version == WHOLE_DISCARD_SACRIFICE_VERSION else {}
    for option in options:
        for order in ((0, 1), (1, 0)):
            first, second = (costs[index].log_kind for index in order)
            label = option.get("label") or "Cast"
            expanded.append({
                **option, **marker,
                "id": option["id"] if order == (0, 1) else f"{option['id']}+reverse-zone-costs",
                "label": f"{label} ({first}, then {second})",
                FIXED_DISCARD_SACRIFICE_ORDER: list(order),
            })
    return expanded


def ordered_discard_sacrifice_selections(option, cost_schema, response):
    """Revalidate both selections together and route each sealed replacement journal."""
    from ..fixed_payment_groups import (
        FIXED_DISCARD_SACRIFICE_ORDER, fixed_discard_sacrifice_costs, fixed_discard_sacrifice_dependencies,
        fixed_discard_sacrifice_order,
    )

    dependencies = fixed_discard_sacrifice_dependencies(cost_schema)
    raw = option.get("additional_costs")
    order = fixed_discard_sacrifice_order(option, raw)
    if not dependencies and not order and FIXED_DISCARD_SACRIFICE_ORDER not in option:
        return ()
    if not dependencies or not order or raw != cost_schema["additional_costs"]:
        raise ValueError("The selected cost order does not match the compiled spell")
    costs = fixed_discard_sacrifice_costs(raw)
    selections = option.get("selected_additional_costs")
    if not isinstance(selections, (list, tuple)) or len(selections) != 2:
        raise ValueError("The cast requires both selected payments")
    by_position = {}
    for selected in selections:
        if not isinstance(selected, Mapping) or set(selected) != {"kind", "operation", "cost_position", "card"}:
            raise ValueError("A mandatory zone cost selection is malformed")
        pos = selected["cost_position"]
        ref = selected["card"]
        if (type(pos) is not int or pos not in (0, 1) or pos in by_position
                or selected["kind"] != ZONE_CHANGE_COST_KIND or selected["operation"] != costs[pos].operation
                or type(ref) is not str or not ref):
            raise ValueError("The two selected zone costs do not match their declared leaves")
        by_position[pos] = selected
    if len({row["card"] for row in by_position.values()}) != 2:
        raise ValueError("The two mandatory payments require distinct objects")

    raw_journal = response.get("_mana_replacement_selections") or {}
    if not isinstance(raw_journal, Mapping):
        raise ValueError("The casting replacement journal is malformed")
    per_position = {0: {}, 1: {}}
    for event, choices in raw_journal.items():
        if type(event) is not str or not isinstance(choices, (list, tuple)):
            raise ValueError("The casting replacement journal is malformed")
        if not event.startswith("zone.change:"):
            continue
        matched = [pos for pos, row in by_position.items() if event.endswith(f":{row['card']}")]
        if len(matched) != 1 or per_position[matched[0]]:
            raise ValueError("A zone replacement journal belongs to exactly one selected payment")
        per_position[matched[0]][event] = tuple(choices)
    return tuple((by_position[pos], {**response, "_mana_replacement_selections": per_position[pos]}) for pos in order)
