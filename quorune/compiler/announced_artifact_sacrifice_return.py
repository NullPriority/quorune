"""Lower a positive announced artifact cost with a power-bounded graveyard target."""

from dataclasses import replace

from ..activation_cost_choice import CostChoice
from ..replacement.immutable import FrozenMap
from .reanimation_templates import FIXED_TARGET_REANIMATION_MECHANIC
from ..rules.announced_artifact_sacrifice_return import (
    ARTIFACT_SACRIFICE_COST_TEXT,
    ARTIFACT_SACRIFICE_RETURN_TEXT,
    ARTIFACT_SACRIFICE_RETURN_TEMPLATE,
    announced_artifact_predicate,
    announced_artifact_return_effect,
    has_announced_artifact_sacrifice,
    owned_graveyard_creature_target_schema,
)


def specialize_announced_artifact_sacrifice(ability):
    if (ability.cost_text != ARTIFACT_SACRIFICE_COST_TEXT or ability.effect_text != ARTIFACT_SACRIFICE_RETURN_TEXT
            or ability.choices or tuple(value.casefold() for value in ability.uncompiled_costs) != ("sacrifice x artifacts",)):
        return ability
    cost = CostChoice(kind="sacrifice_one", count_parameter="x", predicate=FrozenMap(announced_artifact_predicate().to_dict()))
    return replace(ability, choices=(cost,), uncompiled_costs=())


def announced_artifact_sacrifice_return_template(ability, text):
    if not has_announced_artifact_sacrifice(ability) or text != ARTIFACT_SACRIFICE_RETURN_TEXT:
        return None
    return (
        ARTIFACT_SACRIFICE_RETURN_TEMPLATE, (announced_artifact_return_effect(),),
        owned_graveyard_creature_target_schema(), ("cr-115-targets", FIXED_TARGET_REANIMATION_MECHANIC),
    )
