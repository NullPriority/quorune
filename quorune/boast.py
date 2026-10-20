from __future__ import annotations

"""Intrinsic Boast metadata in the existing activation condition/usage model."""

from .activation_condition_model import ActivationCondition,ActivationConditionKind
from .activation_usage import ActivationLimit
from .activation_usage import ACTIVATION_ONCE_PER_TURN_CAPABILITY

BOAST_CAPABILITY='activation.boast.fixed_restrictions'


def keyword_activation_restrictions(prefix,effect_lower,conditions):
    limit=(ActivationLimit.EXHAUST_ONCE if prefix=='exhaust' else ActivationLimit.ONCE_PER_TURN
        if prefix=='boast' or 'only once each turn' in effect_lower else None)
    if prefix!='boast':return limit,conditions,False
    condition=ActivationCondition(ActivationConditionKind.SOURCE_ATTACKED_THIS_TURN)
    return limit,tuple(dict.fromkeys((*conditions,condition))),True


def validate_boast_ability(ability):
    if type(ability.boast) is not bool:raise ValueError('Boast marker must be a boolean')
    if ability.boast and (ability.zones!=('battlefield',) or ability.mana_ability
            or ability.activation_limit is not ActivationLimit.ONCE_PER_TURN
            or not any(c.kind is ActivationConditionKind.SOURCE_ATTACKED_THIS_TURN for c in ability.activation_conditions)):
        raise ValueError('Boast requires its source attack condition and once-per-turn limit')


def boast_runtime_dependencies(program):
    from .abilities import ActivatedAbility
    from .semantic_runtime.activated_abilities import is_structural_activated_ability_catalog_program
    required=set()
    for descriptor in program.handlers:
        if descriptor.get('handler_id')!='activation.catalog.pinned.v1':continue
        raw=descriptor.get('ability',{})
        if 'boast' not in raw and not any(c.get('kind')=='source_attacked_this_turn' for c in raw.get('activation_conditions',())):continue
        ability=ActivatedAbility.from_dict(raw);required.add(BOAST_CAPABILITY)
        if ability.activation_limit is ActivationLimit.ONCE_PER_TURN:required.add(ACTIVATION_ONCE_PER_TURN_CAPABILITY)
    return tuple(sorted(required)) if not is_structural_activated_ability_catalog_program(program) else ()
