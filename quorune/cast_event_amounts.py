"""Closed scalar reads of an exact committed cast spell's public mana value."""

from collections.abc import Mapping
import math

from .query_effect_amount_model import PublicQueryAmountError
from .scalar_effect_amount_model import ScalarAmountOrigin,scalar_amount_specs,SCALAR_AMOUNT_CAPABILITY,SCALAR_AMOUNT_MECHANIC

CAST_MANA_VALUE_CAPABILITY='quantity_expression.cast_spell_mana_value'
CAST_MANA_VALUE_MECHANIC='cast-spell-mana-value-amount'


def cast_mana_value_amount_dependencies(effects,mechanics):
    selected=[spec for spec in scalar_amount_specs(effects) if spec.origin is ScalarAmountOrigin.CAST_SPELL_MANA_VALUE]
    if not selected:
        if CAST_MANA_VALUE_MECHANIC in mechanics:raise ValueError('Cast mana-value marker lacks its represented scalar')
        return ()
    if not {SCALAR_AMOUNT_MECHANIC,CAST_MANA_VALUE_MECHANIC}<=set(mechanics):raise ValueError('Cast spell values require their complete scalar owner')
    return (CAST_MANA_VALUE_CAPABILITY,SCALAR_AMOUNT_CAPABILITY)


def cast_mana_value_runtime_dependencies(program):
    selected=any(spec.origin is ScalarAmountOrigin.CAST_SPELL_MANA_VALUE for spec in scalar_amount_specs(program.effects))
    if not selected:
        if CAST_MANA_VALUE_MECHANIC in program.coverage:raise ValueError('Cast mana-value marker lacks its represented scalar')
        return ()
    if program.event!='spell.cast':raise ValueError('Cast spell mana-value scalars require their normalized cast occurrence')
    return (CAST_MANA_VALUE_CAPABILITY,SCALAR_AMOUNT_CAPABILITY)


def committed_cast_mana_value_facts(item):
    outer=item.context
    context=outer.get('event_context',outer) if isinstance(outer,Mapping) else None
    if not isinstance(context,Mapping) or outer.get('event')!='spell.cast':raise PublicQueryAmountError('Cast spell information requires its original cast event')
    if type(context.get('schema_version')) is not int or context['schema_version'] not in {3,4,5,6,7}:
        raise PublicQueryAmountError('Cast spell mana value requires complete normalized cast facts')
    keys=('logical_object_id','stack','player')
    if any(type(context.get(key)) is not str or not context[key] for key in keys):raise PublicQueryAmountError('Cast spell identity is unavailable')
    amount=context.get('mana_value')
    if type(amount) not in {int,float} or amount<0 or type(amount) is float and (not math.isfinite(amount) or not amount.is_integer()):
        raise PublicQueryAmountError('Cast spell mana value must be a known nonnegative integer')
    return {**{key:context[key] for key in keys},'mana_value':int(amount)}
