from __future__ import annotations

from typing import Mapping
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, BOUND_REFLEXIVE_PROGRAM_VERSIONS, REFLEXIVE_PAYMENT_CAPABILITY,
    REFLEXIVE_PAYMENT_MECHANIC, SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY,
    SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION, ReflexiveTriggerProgram,
)
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, RETURN_PAYMENT_KIND, FixedEffectPaymentSpec
from ..granted_activation_model import (
    GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY, GRANTING_ATTACHMENT_SACRIFICE_REF,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..object_predicate import ObjectQuerySpec
from .hybrid_resolution_payment import HYBRID_RESOLUTION_PAYMENT_KIND


BOUND_DISCARD_ATTACK_CAPABILITY = 'trigger.reflexive.discard_attacking_creature_result'
_BOUND_DISCARD_ATTACK_TARGET = FrozenMap({'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1,
    'types_any': ['creature'], 'combat_state': 'attacking'})
_BOUND_DISCARD_ATTACK_CHILD = (
    FrozenMap({'op': 'place_counters', 'card': '$target.0', 'counter': '+1/+1', 'amount': 1, 'source': '$source'}),
    FrozenMap({'op': 'grant_keyword_until_end_of_turn', 'card': '$target.0', 'keyword': 'Trample'}),
)
_BOUND_DISCARD_ATTACK_DEPS = (
    'combat.damage.assignment.trample', 'continuous.resolution.fixed_characteristics_until_end_of_turn',
    'counter.producer.fixed_effect', 'state_query.permanent.public_state_predicate', 'target.revalidate_resolution',
)


def _bound_discard_attack_child(program):
    return (program.schema_version == BOUND_REFLEXIVE_PROGRAM_VERSION
            and program.target_schema == _BOUND_DISCARD_ATTACK_TARGET
            and type(program.target_schema['count']) is int
            and program.effects == _BOUND_DISCARD_ATTACK_CHILD and type(program.effects[0]['amount']) is int)


def _validate_bound_discard_attack(player, payment, program):
    if (payment.kind != 'discard' or payment.amount != 1
            or payment.predicate != ObjectQuerySpec(zones=('hand',), owner=player, known_to_actor=True)
            or not _bound_discard_attack_child(program)):
        raise ValueError('A bound attack discard requires one owned hand card and its closed attacking target')


def reflexive_payment_parts(effect):
    fields = {'op', 'schema_version', 'player', 'payment', 'reflexive_program'}
    if (not isinstance(effect, Mapping) or set(effect) not in (fields, fields | {'cost'})
            or effect['op'] != 'offer_optional_mana_payment' or type(effect['schema_version']) is not int
            or effect['schema_version'] != 4 or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Reflexive payment instructions use a closed versioned schema')
    payment = FixedEffectPaymentSpec.from_dict(effect['payment'])
    if payment.kind == HYBRID_RESOLUTION_PAYMENT_KIND:
        raise ValueError('Reflexive payment has no hybrid payment authority')
    if payment.kind == RETURN_PAYMENT_KIND:
        raise ValueError('Reflexive payment has no permanent return authority')
    if (('cost' in effect) != (payment.kind == 'mana')
            or payment.kind == 'mana' and dict(effect['cost']) != dict(payment.requirements)):
        raise ValueError('Reflexive payment mana identity changed')
    program = ReflexiveTriggerProgram.from_dict(effect['reflexive_program'])
    if program.schema_version == BOUND_REFLEXIVE_PROGRAM_VERSION:
        if payment.kind == 'discard' or _bound_discard_attack_child(program):
            _validate_bound_discard_attack(effect['player'], payment, program)
        elif payment.kind not in {'mana', 'energy', 'source_sacrifice'}:
            raise ValueError('Bound reflexive triggers require a fixed resource payment or original source sacrifice')
    if payment.kind == 'source_sacrifice' and program.schema_version != BOUND_REFLEXIVE_PROGRAM_VERSION:
        raise ValueError('Reflexive source sacrifice requires its bound parent')
    if payment.source_ref == GRANTING_ATTACHMENT_SACRIFICE_REF and (
        payment.kind != 'source_sacrifice' or not _granting_attachment_damage(program)
    ):
        raise ValueError('A granting Equipment sacrifice requires fixed damage from the attacking creature')
    if program.schema_version == SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION:
        _validate_selected_sacrifice(effect['player'], payment, program)
    return payment, program


def _granting_attachment_damage(program):
    effects = program.effects
    if len(effects) != 1:
        return False
    effect = effects[0]
    return (set(effect) == {'op', 'amount', 'source', 'target'} and effect['op'] == 'damage'
            and type(effect['amount']) is int and effect['amount'] > 0
            and effect['source'] == '$source' and effect['target'] == '$target.0'
            and thaw_value(program.target_schema) == {
                'categories': ['player', 'permanent'], 'count': 1, 'predicate': 'damageable',
                'zones': ['player', 'battlefield'],
            })


def _validate_selected_sacrifice(player, payment, program):
    if (payment.kind != 'sacrifice' or payment.amount != 1
            or payment.predicate.controller != player or payment.predicate.zones != ('battlefield',)):
        raise ValueError('Selected reflexive sacrifices require one controlled permanent')
    if program.uses_paid_creature and not (
        'creature' in payment.predicate.types_all or payment.predicate.types_any == ('creature',)
    ):
        raise ValueError('Reflexive paid power requires a creature sacrifice predicate')


def reflexive_program_capabilities(program):
    from .fixed_effect_payment_shapes import fixed_payment_result_capabilities

    effects = tuple(thaw_value(effect) for effect in program.effects)
    schema = thaw_value(program.target_schema)
    if program.uses_paid_creature:
        return _paid_power_result_capabilities(effects, schema, program.mechanic_ids)
    if _bound_discard_attack_child(program):
        return _BOUND_DISCARD_ATTACK_DEPS
    owned = fixed_payment_result_capabilities(effects=effects, target_schema=schema, mechanic_ids=program.mechanic_ids)
    if owned or program.schema_version not in BOUND_REFLEXIVE_PROGRAM_VERSIONS:
        return owned
    from .linked_exile_return_shapes import linked_exile_return_node_capabilities
    from .role_token_shapes import role_token_node_capabilities

    mechanics = set(program.mechanic_ids)
    return (
        linked_exile_return_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics)
        or role_token_node_capabilities(effects=effects, target_schema=schema, mechanics=mechanics)
        or _live_count_result_capabilities(effects, schema, mechanics)
    )


def _paid_power_result_capabilities(effects, schema, mechanics):
    from ..compiler.scalar_effect_amounts import scalar_amount_shape_context
    from ..scalar_effect_amount_model import (
        SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec,
        scalar_amount_specs,
    )
    from .fixed_effect_payment_shapes import fixed_payment_result_capabilities

    damage = effects[0]
    if (schema is None or SCALAR_AMOUNT_MECHANIC not in mechanics
            or damage.get('op') != 'damage' or damage.get('source') != '$source'
            or damage.get('target') != '$target.0'
            or len(effects) > 2 or len(effects) == 2 and effects[1].get('op') != 'create_token'):
        return ()
    try:
        amount = ScalarEffectAmountSpec.from_dict(damage.get('amount'))
        if amount.origin is not ScalarAmountOrigin.REFLEXIVE_PAID_CREATURE or scalar_amount_specs(effects) != (amount,):
            return ()
    except ValueError:
        return ()
    shape = scalar_amount_shape_context(effects, mechanics)
    if shape is None:
        return ()
    projected, fixed_mechanics = shape
    owned = fixed_payment_result_capabilities(effects=projected, target_schema=schema, mechanic_ids=fixed_mechanics)
    return tuple(sorted({*owned, SCALAR_AMOUNT_CAPABILITY})) if owned else ()


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
    effect, = effects
    operation = effect.get('op')
    if (operation == 'modify_stats_until_end_of_turn' and schema is not None
            and effect.get('power') == effect.get('toughness')):
        field = 'power'
    else:
        field = {'damage': 'amount', 'mill': 'count', 'place_counters': 'amount'}.get(operation)
    if field is None:
        return ()
    try:
        amount = PublicQueryAmountSpec.from_dict(effect[field])
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
        if (effect['player'] != '$controller'
                or payment.kind in {'discard','exile','mana_discard'} and payment.predicate.owner != '$controller'
                or payment.kind in {'sacrifice','tap','blight'} and payment.predicate.controller != '$controller'
                or payment.kind == 'remove_source_counter' and payment.source_ref != '$source.zone_object'
                or payment.kind == 'source_sacrifice' and payment.source_ref not in {
                    '$source.zone_object', GRANTING_ATTACHMENT_SACRIFICE_REF,
                }):
            return ()
        if payment.kind=='sacrifice' and payment.schema_version==6 and payment.predicate.exclude_ref!='$source.zone_object':
            return ()
        child_capabilities = reflexive_program_capabilities(program)
        if not child_capabilities:
            return ()
        selected_sacrifice = ((SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY,)
                              if program.schema_version == SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION else ())
        granting_sacrifice = ((GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY,)
                             if payment.source_ref == GRANTING_ATTACHMENT_SACRIFICE_REF else ())
        discard = (BOUND_DISCARD_ATTACK_CAPABILITY,) if _bound_discard_attack_child(program) else ()
        return tuple(sorted({REFLEXIVE_PAYMENT_CAPABILITY, 'trigger.placement.apnap',
            *selected_sacrifice, *granting_sacrifice, *discard, *payment.capabilities, *child_capabilities}))
    except (KeyError, TypeError, ValueError):
        return ()


def validate_reflexive_paid_power_scope(effects, values, required_capabilities):
    if SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY not in required_capabilities:
        return
    from ..scalar_effect_amount_model import ScalarAmountOrigin, scalar_amount_specs

    if len(effects) == 1:
        try:
            _, child = reflexive_payment_parts(effects[0])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            paid_amounts = [spec for spec in scalar_amount_specs(values)
                            if spec.origin is ScalarAmountOrigin.REFLEXIVE_PAID_CREATURE]
            if child.uses_paid_creature and len(paid_amounts) == 1:
                return
    raise ValueError('Reflexive paid power requires its selected-sacrifice child')


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
    payment, child = reflexive_payment_parts(effects[0])
    if child.schema_version in BOUND_REFLEXIVE_PROGRAM_VERSIONS and not program.ability_id.startswith('trigger:'):
        raise ValueError('Bound reflexive payments require an admitted creating trigger')
    if payment.kind == 'source_sacrifice' and program.active_zone != 'battlefield':
        raise ValueError('Reflexive source sacrifice requires its original battlefield program')
    if BOUND_DISCARD_ATTACK_CAPABILITY in owned and (program.event != 'creature.attacks'
            or program.active_zone != 'battlefield' or program.handlers or program.cost_schema is not None
            or program.event_condition != {'field': 'controller', 'op': 'eq', 'value': '$source.controller'}):
        raise ValueError('A bound attack discard requires its original controller attack declaration')
    if payment.source_ref == GRANTING_ATTACHMENT_SACRIFICE_REF and (
        program.provenance.get('granted_only') is not True or program.event != 'creature.attacks'
        or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
        or program.cost_schema is not None or program.handlers
    ):
        raise ValueError('A granting Equipment sacrifice requires its admitted granted attack trigger')
    return owned
