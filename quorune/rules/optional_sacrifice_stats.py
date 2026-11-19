"""Closed resolution-selected sacrifice price and fixed token result authority."""
from collections.abc import Mapping
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..object_predicate import ObjectQuerySpec
from ..optional_sacrifice_stat_model import (
    ResolutionPaidCreatureStatSpec, RESOLUTION_PAID_STAT_KIND, RESOLUTION_PAID_STAT_CAPABILITY,
    OPTIONAL_SACRIFICE_STAT_WRAPPER_VERSION as VERSION,
)
from ..replacement.immutable import thaw_value

OPTIONAL_SACRIFICE_STAT_MECHANIC = 'optional-sacrifice-creature-stat-result'
_CONDITION = {'all': [{'field': 'player', 'op': 'eq', 'value': '$source.controller'},
                      {'field': 'step', 'op': 'eq', 'value': 'end_step'}]}


def optional_sacrifice_stat_spec(effect, *, actor=None, continuation=False):
    fields = {'op', 'schema_version', 'player', 'payment', 'result', 'stat'}
    if continuation:
        fields |= {'_choice_actor', '_stack_label', '_legal_objects'}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect.get('op') != 'offer_optional_mana_payment'
            or type(effect.get('schema_version')) is not int or effect['schema_version'] != VERSION
            or effect['player'] != ('$controller' if actor is None else actor)):
        raise ValueError('Optional sacrifice stat result requires its complete version-thirteen price frame')
    payment = FixedEffectPaymentSpec.from_dict(effect['payment'])
    expected = ObjectQuerySpec(zones=('battlefield',), controller='$controller' if actor is None else actor,
        types_all=('creature',), token=False)
    if payment.kind != 'sacrifice' or payment.amount != 1 or payment.predicate != expected or payment.schema_version != 2:
        raise ValueError('Stat payment requires exactly one controlled nontoken creature')
    stat = ResolutionPaidCreatureStatSpec.from_dict(effect['stat'])
    result = effect['result']
    if (not isinstance(result, Mapping) or set(result) != {'op', 'controller', 'name', 'quantity', 'characteristics'}
            or result.get('op') != 'create_token' or result['controller'] != effect['player']
            or type(result.get('quantity')) is not int or result['quantity'] != 1):
        raise ValueError('Sacrifice stats require one complete fixed token definition')
    from .token_creation_capability_shapes import _fixed_token_effect_closure
    if _fixed_token_effect_closure({**thaw_value(result), 'controller': '$controller'}) is None:
        raise ValueError('Sacrifice stat result lacks its independent fixed token owner')
    return payment, stat


def optional_sacrifice_stat_node_capabilities(*, effects, target_schema, mechanic_ids=(), cost_schema=None, **kwargs):
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        payment, _ = optional_sacrifice_stat_spec(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    from .token_creation_capability_shapes import _fixed_token_effect_closure
    token, _ = _fixed_token_effect_closure(thaw_value(effects[0]['result']))
    return tuple(sorted({RESOLUTION_PAID_STAT_CAPABILITY, 'trigger.effect.fixed_event',
        *payment.capabilities, *token}))


def _contains_optional_stat(value):
    if isinstance(value, Mapping):
        if (value.get('op') == 'offer_optional_mana_payment' and value.get('schema_version') == VERSION
                or value.get('kind') == RESOLUTION_PAID_STAT_KIND):
            return True
        return any(_contains_optional_stat(v) for v in value.values())
    return isinstance(value, (tuple, list)) and any(_contains_optional_stat(v) for v in value)


def optional_sacrifice_stat_program_dependencies(program):
    if not _contains_optional_stat((program.effects, getattr(program, 'handlers', ()),
            program.target_schema, getattr(program, 'cost_schema', None))):
        return ()
    if (program.event != 'step.begin' or program.active_zone != 'battlefield'
            or getattr(program, 'handlers', ())
            or thaw_value(program.event_condition) != _CONDITION):
        raise ValueError('Sacrifice stat result requires its exact controller end-step occurrence')
    owned = optional_sacrifice_stat_node_capabilities(effects=program.effects, target_schema=program.target_schema,
        cost_schema=getattr(program, 'cost_schema', None))
    if not owned:
        raise ValueError('Sacrifice stat price/result must remain one complete independent instruction')
    return owned


def is_closed_optional_sacrifice_stat_program(program):
    try:
        required = optional_sacrifice_stat_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
