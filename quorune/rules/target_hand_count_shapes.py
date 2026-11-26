from __future__ import annotations

"""One selected public player is the owner of the queried hand cardinality."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import (
    ScalarAmountOrigin, ScalarEffectAmountSpec, scalar_amount_specs, direct_player_amount_target_is_closed,
    TARGET_HAND_COUNT_CAPABILITY,TARGET_HAND_COUNT_MECHANIC,SCALAR_AMOUNT_MECHANIC,SCALAR_AMOUNT_CAPABILITY,
)


UPKEEP_TARGET_HAND_LIFE_VARIANT = "upkeep_target_opposing_hand_life"
UPKEEP_TARGET_HAND_LIFE_TEMPLATE = "fixed-counter-upkeep-target-hand-life-v1"


def upkeep_target_hand_life_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ]}


def upkeep_target_hand_life_schema():
    return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "opponent"}


def upkeep_target_hand_life_effect():
    return {"op": "life", "player": "$controller",
            "delta": ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET_HAND_COUNT, schema_version=4).to_dict()}


def target_hand_count_dependencies(effects,target_schema,mechanics):
    specs=scalar_amount_specs(effects)
    amounts=tuple(spec for spec in specs if spec.origin is ScalarAmountOrigin.TARGET_HAND_COUNT)
    if not amounts:
        if TARGET_HAND_COUNT_MECHANIC in mechanics:
            raise ValueError('Target hand count marker has no represented amount')
        return ()
    if (not {TARGET_HAND_COUNT_MECHANIC,SCALAR_AMOUNT_MECHANIC}<=set(mechanics)
            or not direct_player_amount_target_is_closed(target_schema)
            or len(amounts)!=len(specs)):
        raise ValueError('Target hand count requires one direct player target and its complete amount owner')
    return (TARGET_HAND_COUNT_CAPABILITY,SCALAR_AMOUNT_CAPABILITY,'target.revalidate_resolution')


def target_hand_count_runtime_dependencies(program):
    schema = program.target_schema
    if (
        not program.effects and TARGET_HAND_COUNT_MECHANIC in program.coverage
        and isinstance(schema, Mapping) and isinstance(schema.get("modes"), Mapping)
    ):
        from .modal_node_capabilities import modal_schema_runtime_dependencies

        required = modal_schema_runtime_dependencies(schema)
        if TARGET_HAND_COUNT_CAPABILITY not in required:
            raise ValueError("Modal hand count requires a closed branch amount and its direct target")
        return (TARGET_HAND_COUNT_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "target.revalidate_resolution")
    template = UPKEEP_TARGET_HAND_LIFE_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    expected = FrozenMap(upkeep_target_hand_life_effect())
    effect = FrozenMap(program.effects[0]) if len(program.effects) == 1 else None
    marked = template in str(program.provenance.get("template_id") or "").split("+")
    if marked or program.event == "step.begin" and effect == expected:
        if (
            (program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.cost_schema is not None or program.handlers
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or program.event_condition is None
            or FrozenMap(program.event_condition) != FrozenMap(upkeep_target_hand_life_condition())
            or not direct_player_amount_target_is_closed(program.target_schema)
            or FrozenMap(program.target_schema) != FrozenMap(upkeep_target_hand_life_schema())
            or effect != expected
        ):
            raise ValueError("Upkeep hand-count life requires the original opposing target and controller")
        return (*target_hand_count_dependencies(program.effects, program.target_schema, program.coverage),
                "life.change.effect", "trigger.effect.fixed_event", "trigger.placement.apnap")
    return target_hand_count_dependencies(program.effects,program.target_schema,program.coverage)
