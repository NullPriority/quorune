from __future__ import annotations

"""Shared promotion boundaries for closed public graveyard movements."""

from ..rules.reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
from ..rules.graveyard_card_targets import targeted_own_graveyard_return_node_capabilities


def is_closed_reanimation_effect_program(program):
    required=fixed_target_reanimation_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def is_closed_own_graveyard_return_program(program):
    required=targeted_own_graveyard_return_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=(v for v in program.coverage if v in {'return-to-owner-hand','cr-115-targets'}))
    return bool(required) and set(required)<=set(program.capability_dependencies)
