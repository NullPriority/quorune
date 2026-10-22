from __future__ import annotations
from typing import Mapping

"""Closed controller-only Ascend instruction shape."""

from ..city_blessing_model import CITYS_BLESSING_CAPABILITY


def ascend_spell_node_capabilities(*,effects,target_schema,mechanic_ids,**_unused):
    if 'ascend' not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    return (CITYS_BLESSING_CAPABILITY,) if isinstance(effect,Mapping) and dict(effect)=={'op':'ascend','player':'$controller'} else ()


def ascend_program_is_closed(program):
    required=ascend_spell_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def city_activation_runtime_dependencies(program):
    from ..activation_condition_model import ActivationConditionKind
    from ..semantic_runtime.activated_abilities import (
        activated_abilities_from_descriptors, is_structural_activated_ability_catalog_program,
    )
    if program.event != 'activate' or is_structural_activated_ability_catalog_program(program):
        return ()
    abilities = activated_abilities_from_descriptors(program.handlers)
    return ('activation.condition.citys_blessing',) if any(
        condition.kind is ActivationConditionKind.CONTROLLER_HAS_CITYS_BLESSING
        for ability in abilities for condition in ability.activation_conditions) else ()
