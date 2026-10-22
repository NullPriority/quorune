from __future__ import annotations

"""Expand independently represented optional additional mana branches."""

import copy


def expand_optional_mana_costs(host, card, base, schema, *, response=None, hint=False):
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
            metadata = {key: copy.deepcopy(value) for key, value in additional.items() if key not in {'requirements', 'id'}}
            if base_option.get('_additional_option_costs') and additional.get('_additional_option_costs'):
                metadata['_additional_option_costs'] = [*copy.deepcopy(base_option['_additional_option_costs']),
                    *copy.deepcopy(additional['_additional_option_costs'])]
            expanded.append(
                {
                    **base_option,
                    **metadata,
                    **({'label': str(base_option.get('label') or '')+' + '+str(additional.get('label') or additional['id'])}
                       if base_option.get('battlefield_alternative_cost') else {}),
                    "id": (str(base_option['id'])+'+'+str(additional['id'])
                           if base_option.get('battlefield_alternative_cost') else str(additional["id"])),
                    "kind": str(
                        additional.get("kind") or "optional_additional"
                    ),
                    "requirements": combined,
                    "base_cost_option": base_option["id"],
                }
            )
    from .paid_entry_copy_costs import expand_paid_entry_copy_costs
    from .multikicker_costs import expand_multikicker_costs
    expanded = expand_paid_entry_copy_costs(expanded, schema, response=response, hint=hint)
    expanded = expand_multikicker_costs(expanded, schema, response=response, hint=hint)
    from .replicate_costs import expand_replicate_costs
    return expand_replicate_costs(expanded, schema, response=response, hint=hint)
