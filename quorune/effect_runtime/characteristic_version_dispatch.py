from __future__ import annotations

"""Dispatch versioned temporary characteristics to their coherent owners."""


def apply_versioned_characteristic_effect(host,effect,*,actor,reason):
    version=effect.get('schema_version')
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
    if version==3:
        from ..rules.defender_permission_effect import apply_temporary_defender_permission
        return True,apply_temporary_defender_permission(host,effect,actor=actor,reason=reason)
    if version==2:
        from ..continuous_effect_state import apply_fixed_resolution_characteristics
        return True,apply_fixed_resolution_characteristics(host,effect,actor=actor,reason=reason)
    return False,None
