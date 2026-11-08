from __future__ import annotations

from typing import Mapping
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_CAPABILITY, REFLEXIVE_PAYMENT_MECHANIC, ReflexiveTriggerProgram,
)
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..replacement.immutable import thaw_value
from .hybrid_resolution_payment import HYBRID_RESOLUTION_PAYMENT_KIND


def reflexive_payment_parts(effect):
    fields = {'op', 'schema_version', 'player', 'payment', 'reflexive_program'}
    if (not isinstance(effect, Mapping) or set(effect) not in (fields, fields | {'cost'})
            or effect['op'] != 'offer_optional_mana_payment' or type(effect['schema_version']) is not int
            or effect['schema_version'] != 4 or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Reflexive payment instructions use a closed versioned schema')
    payment = FixedEffectPaymentSpec.from_dict(effect['payment'])
    if payment.kind == HYBRID_RESOLUTION_PAYMENT_KIND:
        raise ValueError('Reflexive payment has no hybrid payment authority')
    if (('cost' in effect) != (payment.kind == 'mana')
            or payment.kind == 'mana' and dict(effect['cost']) != dict(payment.requirements)):
        raise ValueError('Reflexive payment mana identity changed')
    program = ReflexiveTriggerProgram.from_dict(effect['reflexive_program'])
    if program.schema_version == BOUND_REFLEXIVE_PROGRAM_VERSION and payment.kind not in {'mana', 'energy'}:
        raise ValueError('Bound reflexive triggers require one fixed mana or energy payment')
    return payment, program


def reflexive_program_capabilities(program):
    from .fixed_effect_payment_shapes import fixed_payment_result_capabilities

    effects = tuple(thaw_value(effect) for effect in program.effects)
    schema = thaw_value(program.target_schema)
    owned = fixed_payment_result_capabilities(effects=effects, target_schema=schema, mechanic_ids=program.mechanic_ids)
    if owned or program.schema_version != BOUND_REFLEXIVE_PROGRAM_VERSION:
        return owned
    from .linked_exile_return_shapes import linked_exile_return_node_capabilities
    from .role_token_shapes import role_token_node_capabilities

    mechanics = set(program.mechanic_ids)
    return (
        linked_exile_return_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics)
        or role_token_node_capabilities(effects=effects, target_schema=schema, mechanics=mechanics)
        or _live_count_result_capabilities(effects, schema, mechanics)
    )


def _live_count_result_capabilities(effects, schema, mechanics):
    from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from ..compiler.declared_effect_amounts import declared_amount_dependencies
    from ..compiler.public_query_effect_amounts import (
        PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, public_query_amount_shape_context,
    )
    from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec
    from .fixed_effect_payment_shapes import fixed_payment_result_capabilities

    if len(effects) != 1 or PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC not in mechanics:
        return ()
    field = {'damage': 'amount', 'mill': 'count', 'place_counters': 'amount'}.get(effects[0].get('op'))
    if field is None:
        return ()
    try:
        amount = PublicQueryAmountSpec.from_dict(effects[0][field])
    except (KeyError, TypeError, ValueError):
        return ()
    quantity = amount.quantity
    if (not isinstance(quantity, CharacteristicQuantitySpec) or quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
            or quantity.query is None or quantity.query.zones != ('battlefield',)
            or quantity.counter_name is not None or amount.coefficient != 1):
        return ()
    shape = public_query_amount_shape_context(effects, mechanics, target_schema=schema)
    if shape is None:
        return ()
    projected, fixed_mechanics = shape
    owned = fixed_payment_result_capabilities(effects=projected, target_schema=schema, mechanic_ids=fixed_mechanics)
    if not owned:
        return ()
    dependencies = declared_amount_dependencies(mechanics, effects=effects)
    return tuple(sorted({*owned, *dependencies, PUBLIC_QUERY_AMOUNT_CAPABILITY}))


def reflexive_payment_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or not {FIXED_EFFECT_PAYMENT_MECHANIC, REFLEXIVE_PAYMENT_MECHANIC} <= set(mechanic_ids)):
        return ()
    try:
        effect = effects[0]
        payment, program = reflexive_payment_parts(effect)
        if payment.kind=='source_sacrifice':return ()
        if (effect['player'] != '$controller'
                or payment.kind in {'discard','exile','mana_discard'} and payment.predicate.owner != '$controller'
                or payment.kind in {'sacrifice','tap','blight'} and payment.predicate.controller != '$controller'
                or payment.kind=='remove_source_counter' and payment.source_ref!='$source.zone_object'):
            return ()
        if payment.kind=='sacrifice' and payment.schema_version==6 and payment.predicate.exclude_ref!='$source.zone_object':
            return ()
        child_capabilities = reflexive_program_capabilities(program)
        if not child_capabilities:
            return ()
        return tuple(sorted({REFLEXIVE_PAYMENT_CAPABILITY, 'trigger.placement.apnap',
            *payment.capabilities, *child_capabilities}))
    except (KeyError, TypeError, ValueError):
        return ()


def reflexive_payment_runtime_dependencies(program):
    """Independently bind the cost and later effect of a reflexive instruction."""
    effects = program.effects
    if not any(effect.get('op') == 'offer_optional_mana_payment' and effect.get('schema_version') == 4 for effect in effects):
        return ()
    owned = reflexive_payment_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError('Reflexive payment requires a closed cost and an independently owned child')
    _, child = reflexive_payment_parts(effects[0])
    if child.schema_version == BOUND_REFLEXIVE_PROGRAM_VERSION and not program.ability_id.startswith('trigger:'):
        raise ValueError('Bound reflexive payments require an admitted creating trigger')
    return owned
