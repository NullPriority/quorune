from __future__ import annotations

"""Expand independently represented optional additional mana branches."""

import copy


def expand_optional_mana_costs(host, card, base, schema):
    expanded = list(base)
    for raw in schema.get("optional_costs", []):
        additional = dict(raw)
        if (
            additional.get("source_zone") is not None
            and str(additional["source_zone"]) != card.zone
        ):
            continue
        additional_vector = host._mana_vector(additional.get("requirements"))
        for base_option in base:
            combined = host._mana_vector(base_option["requirements"])
            for symbol, amount in additional_vector.items():
                combined[symbol] += amount
            expanded.append(
                {
                    **base_option,
                    **{
                        key: copy.deepcopy(value)
                        for key, value in additional.items()
                        if key not in {"requirements", "id"}
                    },
                    "id": str(additional["id"]),
                    "kind": str(
                        additional.get("kind") or "optional_additional"
                    ),
                    "requirements": combined,
                    "base_cost_option": base_option["id"],
                }
            )
    from .paid_entry_copy_costs import expand_paid_entry_copy_costs
    return expand_paid_entry_copy_costs(expanded, schema)
