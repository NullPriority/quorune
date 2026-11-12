from __future__ import annotations

"""Dispatch versioned temporary characteristics to their coherent owners."""

from ..resolution_characteristic_model import FIXED_CHARACTERISTIC_INSTRUCTION_VERSIONS, FIXED_TARGET_COLOR_VERSION
from ..defender_permission import is_temporary_defender_permission
from ..rules.power_toughness_switch import is_power_toughness_switch
from ..rules.fixed_stat_multipliers import is_fixed_stat_multiplier


def apply_versioned_characteristic_effect(host,effect,*,actor,reason):
    version=effect.get('schema_version')
    if version == FIXED_TARGET_COLOR_VERSION:
        from ..continuous_effect_state import apply_fixed_target_color
        return True, apply_fixed_target_color(host, effect, actor=actor, reason=reason)
    if is_power_toughness_switch(effect):
        from ..continuous_effect_state import apply_power_toughness_switch
        return True, apply_power_toughness_switch(host, effect, actor=actor, reason=reason)
    if is_fixed_stat_multiplier(effect):
        from ..rules.fixed_stat_multipliers import apply_fixed_stat_multiplier
        return True, apply_fixed_stat_multiplier(host, effect, actor=actor, reason=reason)
    if version==11:
        from ..rules.temporary_keyword_removal import apply_temporary_keyword_removal
        return True,apply_temporary_keyword_removal(host,effect,actor=actor,reason=reason)
    if version == 10:
        from ..rules.block_capacity_effect import apply_temporary_block_capacity
        return True, apply_temporary_block_capacity(host, effect, actor=actor, reason=reason)
    if version in {7, 8, 9}:
        from .temporary_ability_bundle import apply_temporary_ability_bundle
        return True,apply_temporary_ability_bundle(host,effect,actor=actor,reason=reason)
    if version==6:
        from ..continuous_effect_state import apply_target_characteristics
        return True,apply_target_characteristics(host,effect,actor=actor,reason=reason)
    if version==5:
        from ..rules.toughness_assignment_effect import apply_temporary_toughness_assignment
        return True,apply_temporary_toughness_assignment(host,effect,actor=actor,reason=reason)
    if version==4:
        from ..rules.as_unblocked_effect import apply_temporary_as_unblocked
        return True,apply_temporary_as_unblocked(host,effect,actor=actor,reason=reason)
    if is_temporary_defender_permission(effect):
        from ..rules.defender_permission_effect import apply_temporary_defender_permission
        return True,apply_temporary_defender_permission(host,effect,actor=actor,reason=reason)
    if type(version) is int and version in FIXED_CHARACTERISTIC_INSTRUCTION_VERSIONS:
        from ..continuous_effect_state import apply_fixed_resolution_characteristics
        return True,apply_fixed_resolution_characteristics(host,effect,actor=actor,reason=reason)
    return False,None
