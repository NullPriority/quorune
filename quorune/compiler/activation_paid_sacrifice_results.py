"""Use paid creature facts only in already-owned nonmana result slots."""
from collections.abc import Mapping
from dataclasses import replace

from ..activation_condition_model import PublicActivationTiming
from ..activation_paid_characteristic_model import (
    ACTIVATION_PAID_STAT_MECHANIC,
)
from ..rules.activation.paid_characteristic_price import activation_creature_sacrifice_choice
from ..scalar_effect_amount_model import (
    PAID_SACRIFICE_STAT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec, SCALAR_AMOUNT_KIND,
)
from .activated_costs import activated_ability_cost
from .paid_sacrifice_amount_templates import _paid_sacrifice_stat_template


def compile_activation_result(ability, *, source_name, text, compile_fixed, public_timing=None):
    """Choose an owned activation quantity before the ordinary fixed result."""
    if (public_timing is PublicActivationTiming.ANY_UPKEEP
            and text.casefold().endswith(" counter from this artifact.")):
        from .counter_removal_templates import fixed_source_counter_removal_effect_template
        result = fixed_source_counter_removal_effect_template(text, card_name=source_name)
        if result is not None:
            return result
    from .activation_x_costs import announced_x_activation_effect
    from .temporary_keyword_removal_templates import chosen_temporary_keyword_removal_template
    if ("third time this ability has resolved this turn" in text.casefold()
            and not ability.mana_ability and ability.zones == ("battlefield",)
            and not ability.sorcery_speed and not ability.activation_conditions):
        from .resolution_condition_templates import third_ability_result_template
        if ordinal := third_ability_result_template(
            text, compile_component=compile_fixed,
            cost_schema=activated_ability_cost(ability, public_timing=public_timing),
        ):
            return ordinal
    from .graveyard_exile_copy import graveyard_exile_self_copy_template
    from .temporary_self_copy_templates import announced_graveyard_self_copy_template, linked_exile_self_copy_template

    return (announced_graveyard_self_copy_template(ability, source_name=source_name, text=text, public_timing=public_timing)
        or graveyard_exile_self_copy_template(ability, source_name=source_name, text=text, public_timing=public_timing)
        or linked_exile_self_copy_template(ability, source_name=source_name, text=text, public_timing=public_timing)
        or chosen_temporary_keyword_removal_template(ability, text=text, source_name=source_name)
        or activation_paid_sacrifice_result(ability, source_name=source_name,
                text=text, compile_fixed=compile_fixed)
        or announced_x_activation_effect(ability, source_name=source_name,
                text=text, compile_fixed=compile_fixed)
        or compile_fixed(text))


def activation_paid_sacrifice_result(ability, *, source_name, text, compile_fixed):
    cost = activated_ability_cost(ability)
    if ability.mana_ability or activation_creature_sacrifice_choice(cost) is None:
        return None
    from .paid_sacrifice_condition_templates import paid_sacrifice_condition_template

    if conditioned := paid_sacrifice_condition_template(
        text, cost_schema=cost, compile_fixed=compile_fixed, activation=True, source_name=source_name,
    ):
        return conditioned
    compiled = _paid_sacrifice_stat_template(text, source_name=source_name, compile_fixed=compile_fixed)
    if compiled is None:
        return None
    template, effects, schema, mechanics = compiled
    if not effects or any(effect.get('op') == 'mana' for effect in effects):
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
