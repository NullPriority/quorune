from __future__ import annotations

"""One selected public player is the owner of the queried hand cardinality."""

from ..scalar_effect_amount_model import (
    ScalarAmountOrigin,scalar_amount_specs,direct_player_amount_target_is_closed,
    TARGET_HAND_COUNT_CAPABILITY,TARGET_HAND_COUNT_MECHANIC,SCALAR_AMOUNT_MECHANIC,SCALAR_AMOUNT_CAPABILITY,
)


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
    return target_hand_count_dependencies(program.effects,program.target_schema,program.coverage)
