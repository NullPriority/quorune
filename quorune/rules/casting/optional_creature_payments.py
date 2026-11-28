"""Price options for an independently selected number of controlled creatures."""

from __future__ import annotations

from collections.abc import Mapping

from ..casting_additional_costs import (
    CAST_COPY_SACRIFICE_PRICE_VERSION,
    FixedZoneChangeAdditionalCost,
    damage_qualified_sacrifice_predicate,
    fixed_zone_change_cost_candidates,
)


SACRIFICE_CREDIT_COST = "optional_creature_sacrifice_credit"
SACRIFICE_CREDIT_CAPABILITY = "casting.additional_cost.optional_creature_sacrifice_credit"
SACRIFICE_CREDIT_AMOUNT = "_creature_sacrifice_generic_credit"
_GENERIC_CREDIT_PER_CREATURE = 2


def sacrifice_credit_cost_schema():
    return {SACRIFICE_CREDIT_COST: {"schema_version": 1, "generic_per_creature": _GENERIC_CREDIT_PER_CREATURE}}


def sacrifice_credit_dependencies(cost_schema):
    if not isinstance(cost_schema, Mapping) or SACRIFICE_CREDIT_COST not in cost_schema:
        return ()
    price = cost_schema[SACRIFICE_CREDIT_COST]
    if (
        set(cost_schema) != {SACRIFICE_CREDIT_COST}
        or not isinstance(price, Mapping)
        or set(price) != {"schema_version", "generic_per_creature"}
        or type(price["schema_version"]) is not int
        or price["schema_version"] != 1
        or type(price["generic_per_creature"]) is not int
        or price["generic_per_creature"] != _GENERIC_CREDIT_PER_CREATURE
    ):
        raise ValueError("Optional sacrifice credit requires its exact controlled-creature price")
    return (SACRIFICE_CREDIT_CAPABILITY, "casting.additional_cost.fixed_sacrifice")


def controlled_creature_payment(count):
    return FixedZoneChangeAdditionalCost(
        operation="sacrifice_one", choice_field="sacrifice_cards",
        predicate=damage_qualified_sacrifice_predicate(),
        schema_version=CAST_COPY_SACRIFICE_PRICE_VERSION, count=count,
    )


def expand_counted_creature_payment_options(host, seat, source, options, *, marker, generic_per_creature=0):
    candidates = fixed_zone_change_cost_candidates(
        host, actor=seat, cost=controlled_creature_payment(1), exclude_object_id=source.object_id,
    )
    paid = []
    for option in options:
        for count in range(1, len(candidates) + 1):
            price = controlled_creature_payment(count).to_descriptor()
            count_option = {
                **option,
                "id": f"{option['id']}{marker}{count}",
                "label": f"{option.get('label') or 'Cast'} and sacrifice {count} creature{'s' if count != 1 else ''}",
                "_additional_option_costs": [*option.get("_additional_option_costs", ()), price],
            }
            if generic_per_creature:
                count_option[SACRIFICE_CREDIT_AMOUNT] = generic_per_creature * count
            paid.append(count_option)
    return [*options, *paid]


def expand_sacrifice_credit_options(host, seat, source, options, cost_schema):
    if not sacrifice_credit_dependencies(cost_schema):
        return options
    return expand_counted_creature_payment_options(
        host, seat, source, options, marker="+creature-sacrifice-credit:",
        generic_per_creature=_GENERIC_CREDIT_PER_CREATURE,
    )
