"""Recover's exact graveyard occurrence, fixed price and original-card result."""
from collections.abc import Mapping
from ..recover_model import (RecoverSpec, RECOVER_CAPABILITY, RECOVER_RESULT_CAPABILITY,
    RECOVER_PAYMENT_WRAPPER_VERSION, RECOVER_RESULT_OPERATION)
from .fractional_life_payment import FractionalLifeRecoverSpec, FRACTIONAL_RECOVER_KIND, FRACTIONAL_RECOVER_CAPABILITY
from ..fractional_life_payment_model import FRACTIONAL_LIFE_PAYMENT_CAPABILITY

RECOVER_EVENT = 'creature.dies'
RECOVER_CONDITION = {'field': 'owner', 'op': 'eq', 'value': '$source.owner'}


def recover_payment_effect(spec):
    if isinstance(spec, FractionalLifeRecoverSpec):
        return {'op': 'offer_optional_mana_payment', 'schema_version': RECOVER_PAYMENT_WRAPPER_VERSION,
            'player': '$controller', 'recover': spec.to_dict()}
    if not isinstance(spec, RecoverSpec):
        raise ValueError('Recover requires its closed price')
    return {'op': 'offer_optional_mana_payment', 'schema_version': RECOVER_PAYMENT_WRAPPER_VERSION,
            'player': '$controller', 'recover': spec.to_dict(), 'cost': dict(spec.payment.requirements)}


def recover_payment_spec(effect, *, actor=None, continuation=False):
    fraction = isinstance(effect, Mapping) and isinstance(effect.get('recover'), Mapping) and effect['recover'].get('kind') == FRACTIONAL_RECOVER_KIND
    fields = {'op', 'schema_version', 'player', 'recover'} | (set() if fraction else {'cost'})
    if continuation:
        fields |= {'_choice_actor', '_stack_label', '_legal_objects', '_recover_identity'}
        if fraction:fields.add('_fractional_life_quote')
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect.get('op') != 'offer_optional_mana_payment'
            or type(effect.get('schema_version')) is not int or effect['schema_version'] != RECOVER_PAYMENT_WRAPPER_VERSION
            or effect['player'] != ('$controller' if actor is None else actor)):
        raise ValueError('Recover payment fields or controller are malformed')
    if fraction:return FractionalLifeRecoverSpec.from_dict(effect['recover'])
    spec = RecoverSpec.from_dict(effect['recover'])
    if not isinstance(effect['cost'], Mapping) or dict(effect['cost']) != dict(spec.payment.requirements):
        raise ValueError('Recover payment cost identity changed')
    return spec


def recover_result_effect(*, actor, identity, paid):
    if type(paid) is not bool:
        raise ValueError('Recover requires an explicit payment outcome')
    effect = {'op': RECOVER_RESULT_OPERATION, 'schema_version': 1, 'player': actor,
              'identity': identity, 'card': '$source.graveyard_object',
              'destination': 'hand' if paid else 'exile'}
    validate_recover_result(effect)
    return effect


def validate_recover_result(effect, *, resolved=False):
    fields = {'op', 'schema_version', 'player', 'identity', 'card', 'destination'}
    optional = {'reason', '_runtime_source', '_replacement_selections'} if resolved else set()
    if (not isinstance(effect, Mapping) or set(effect) - optional != fields
            or effect['op'] != RECOVER_RESULT_OPERATION
            or type(effect['schema_version']) is not int or effect['schema_version'] != 1
            or any(type(effect[key]) is not str or not effect[key] for key in ('player', 'identity'))
            or effect['destination'] not in {'hand', 'exile'}
            or not resolved and effect['card'] != '$source.graveyard_object'
            or resolved and effect['card'] is not None and (type(effect['card']) is not str or not effect['card'])):
        raise ValueError('Recover result must retain its exact original graveyard source')
    return (RECOVER_RESULT_CAPABILITY, 'zone.change.destination_replacement')


def recover_node_dependencies(effect):
    spec = recover_payment_spec(effect)
    if isinstance(spec, FractionalLifeRecoverSpec):
        return tuple(sorted({FRACTIONAL_RECOVER_CAPABILITY, FRACTIONAL_LIFE_PAYMENT_CAPABILITY,
            RECOVER_RESULT_CAPABILITY, 'effect.choice.optional_fixed_mana_payment',
            'trigger.event.normalized_zone_change', 'zone.change.destination_replacement'}))
    return tuple(sorted({RECOVER_CAPABILITY, RECOVER_RESULT_CAPABILITY,
        'trigger.event.normalized_zone_change', 'zone.change.destination_replacement', *spec.payment.capabilities}))


def recover_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if not any(e.get('op') == 'offer_optional_mana_payment'
               and e.get('schema_version') == RECOVER_PAYMENT_WRAPPER_VERSION for e in effects):
        return ()
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        raise ValueError('Recover requires one targetless resolution payment')
    return recover_node_dependencies(effects[0])


def recover_program_dependencies(program):
    from ..replacement.immutable import thaw_value
    effects = program.effects
    def contains(value):
        if isinstance(value, Mapping):
            if (value.get('op') == 'offer_optional_mana_payment'
                    and value.get('schema_version') == RECOVER_PAYMENT_WRAPPER_VERSION
                    or value.get('op') == RECOVER_RESULT_OPERATION):
                return True
            return any(contains(v) for v in value.values())
        return isinstance(value, (tuple, list)) and any(contains(v) for v in value)
    if not contains((effects, getattr(program, 'handlers', ()), program.cost_schema, program.target_schema)):
        return ()
    owned = [e for e in effects if e.get('op') == 'offer_optional_mana_payment'
             and e.get('schema_version') == RECOVER_PAYMENT_WRAPPER_VERSION]
    results = [e for e in effects if e.get('op') == RECOVER_RESULT_OPERATION]
    if (len(effects) != 1 or len(owned) != 1 or results or program.event != RECOVER_EVENT
            or program.active_zone != 'graveyard' or thaw_value(program.event_condition) != RECOVER_CONDITION
            or program.target_schema is not None or program.cost_schema is not None):
        raise ValueError('Recover requires its exact owner-graveyard creature-death frame and one complete price')
    return recover_node_dependencies(owned[0])


def is_closed_recover_program(program):
    try:
        required = recover_program_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
