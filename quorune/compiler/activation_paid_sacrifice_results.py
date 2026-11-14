"""Use the existing paid-stat grammar for a closed activation life result."""
from collections.abc import Mapping
from dataclasses import replace

from ..activation_paid_characteristic_model import (
    ACTIVATION_PAID_STAT_MECHANIC,
)
from ..rules.activation.paid_characteristic_price import activation_creature_sacrifice_choice
from ..scalar_effect_amount_model import (
    PAID_SACRIFICE_STAT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec, SCALAR_AMOUNT_KIND,
)
from .activated_costs import activated_ability_cost
from .paid_sacrifice_amount_templates import _paid_sacrifice_stat_template


def compile_activation_result(ability, *, source_name, text, compile_fixed):
    """Choose an owned activation quantity before the ordinary fixed result."""
    from .activation_x_costs import announced_x_activation_effect
    return (activation_paid_sacrifice_result(ability, source_name=source_name,
                text=text, compile_fixed=compile_fixed)
        or announced_x_activation_effect(ability, source_name=source_name,
                text=text, compile_fixed=compile_fixed)
        or compile_fixed(text))


def activation_paid_sacrifice_result(ability, *, source_name, text, compile_fixed):
    if ability.mana_ability or activation_creature_sacrifice_choice(activated_ability_cost(ability)) is None:
        return None
    compiled = _paid_sacrifice_stat_template(text, source_name=source_name, compile_fixed=compile_fixed)
    if compiled is None:
        return None
    template, effects, schema, mechanics = compiled
    if (schema is not None or len(effects) != 1 or effects[0].get('op') != 'life'
            or effects[0].get('player') != '$controller'):
        return None
    def convert(value):
        if isinstance(value, Mapping):
            if value.get('kind') == SCALAR_AMOUNT_KIND:
                spec = ScalarEffectAmountSpec.from_dict(value)
                if spec.origin is not ScalarAmountOrigin.PAID_SACRIFICE_CREATURE:
                    raise ValueError('Activation paid result requires its isolated paid creature stat')
                return replace(spec, origin=ScalarAmountOrigin.PAID_ACTIVATION_CREATURE, schema_version=13).to_dict()
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(convert(v) for v in value)
        return value
    return ('activation-' + template, tuple(convert(e) for e in effects), schema,
        tuple(m for m in mechanics if m != PAID_SACRIFICE_STAT_MECHANIC) + (ACTIVATION_PAID_STAT_MECHANIC,))
