from __future__ import annotations

"""One closed mana instruction, with independent producer authority."""

from typing import Mapping
from ..public_quantity_mana_model import PUBLIC_QUANTITY_MANA_CAPABILITY
from ..query_effect_amount_model import PublicQueryAmountSpec, PUBLIC_QUERY_AMOUNT_KIND
from ..scalar_effect_amount_model import ScalarEffectAmountSpec, SCALAR_AMOUNT_KIND, ScalarAmountOrigin


def public_quantity_mana_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    del kwargs
    markers = set(mechanic_ids)
    if target_schema is not None or len(effects) != 1 or 'public-quantity-mana' not in markers:
        return ()
    effect = effects[0]
    if set(effect) != {'op','player','color','amount','source'} or effect['op'] != 'mana' or effect['player'] != '$controller' or effect['source'] != '$source':
        return ()
    if type(effect['color']) is not str or effect['color'] not in 'WUBRGC' or len(effect['color']) != 1:
        return ()
    value = effect['amount']
    if not isinstance(value, Mapping):
        return ()
    try:
        if value.get('kind') == PUBLIC_QUERY_AMOUNT_KIND:
            spec = PublicQueryAmountSpec.from_dict(value)
            marker = 'public-query-effect-amount'
            if spec.coefficient != 1 or spec.schema_version != 1:
                return ()
        elif value.get('kind') == SCALAR_AMOUNT_KIND:
            spec = ScalarEffectAmountSpec.from_dict(value)
            marker = 'scalar-effect-amount'
            if spec.origin not in {ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.HISTORY} or spec.coefficient != 1 or spec.binding_id is not None:
                return ()
        else:
            return ()
    except (TypeError, ValueError):
        return ()
    if marker not in markers:
        return ()
    if markers - {'public-quantity-mana', marker, 'generated_oracle_ir', 'spell_resolution',
                  'activated_ability', 'triggered_ability', 'cr-603-handling-triggered-abilities'}:
        return ()
    producer = 'quantity_expression.public_query_effect_amount' if marker == 'public-query-effect-amount' else 'quantity_expression.scalar_effect_amount'
    trigger = ('trigger.placement.apnap',) if 'cr-603-handling-triggered-abilities' in markers else ()
    return (PUBLIC_QUANTITY_MANA_CAPABILITY, producer, *trigger)


def is_closed_public_quantity_mana_program(program):
    required = public_quantity_mana_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
