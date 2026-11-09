from __future__ import annotations

"""Assemble represented optional mana and resource prices in their fixed order."""

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


def expand_optional_resource_costs(host, seat, card, expanded, *, suppress_source_costs, mechanics, mandatory,
                                   cast_type_line=None):
    """Existing price owners retain their own selection and composition guards."""
    from ...semantic_runtime.spell_payment_grants import (
        SPELL_PAYMENT_GRANT_EVENT, SPELL_PAYMENT_GRANT_FIELD, active_spell_payment_grants, granted_payment_mechanics,
        matching_spell_payment_kinds,
    )
    from .source_placement import cast_payment_read_host

    grants = ()
    if host.semantics.programs_for_event(SPELL_PAYMENT_GRANT_EVENT):
        payment_host = cast_payment_read_host(host, seat)
        grants = active_spell_payment_grants(payment_host, seat)
    if not grants:
        return _expand_resource_costs(host, seat, card, expanded, suppress_source_costs=suppress_source_costs,
                                      mechanics=mechanics, mandatory=mandatory)
    intrinsic = {str(value.get("kind") or "").casefold() for value in mechanics}
    matches = {}
    result = []
    for option in expanded:
        type_line = option.get("cast_type_line") or cast_type_line
        if type_line not in matches:
            kinds = matching_spell_payment_kinds(payment_host, seat, card, grants, origin=card.zone,
                cast_type_line=type_line, face_down=suppress_source_costs)
            matches[type_line] = [kind for kind in kinds if kind not in intrinsic]
        added = matches[type_line]
        priced = _expand_resource_costs(host, seat, card, [option], suppress_source_costs=suppress_source_costs,
            mechanics=[*mechanics, *granted_payment_mechanics(added)], mandatory=mandatory)
        if added:
            priced = [{**value, SPELL_PAYMENT_GRANT_FIELD: added} for value in priced]
        result.extend(priced)
    return result


def _expand_resource_costs(host, seat, card, expanded, *, suppress_source_costs, mechanics, mandatory):
    from .conspire_costs import expand_conspire_options
    expanded = expand_conspire_options(host, seat, card, expanded, suppress_source_costs=suppress_source_costs,
        mechanics=mechanics, mandatory=mandatory)
    from .casualty_costs import expand_casualty_options
    expanded = expand_casualty_options(host, seat, card, expanded, suppress_source_costs=suppress_source_costs, mechanics=mechanics, mandatory=mandatory)
    from .bargain_costs import expand_bargain_options
    expanded = expand_bargain_options(host, seat, card, expanded, suppress_source_costs=suppress_source_costs, mechanics=mechanics, mandatory=mandatory)
    return expanded


def bind_granted_payment_mechanics(option, mechanics, response, *, hint):
    from ...semantic_runtime.spell_payment_grants import SPELL_PAYMENT_GRANT_FIELD, granted_payment_mechanics

    granted = option.pop(SPELL_PAYMENT_GRANT_FIELD, ())
    if not granted:
        return mechanics
    used = {kind: response[f"{kind}_cards"] for kind in granted if not hint and response.get(f"{kind}_cards")}
    if used:
        option[SPELL_PAYMENT_GRANT_FIELD] = used
    return [*mechanics, *granted_payment_mechanics(granted)]
