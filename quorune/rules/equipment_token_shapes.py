from __future__ import annotations

"""Closed noncreature Equipment definitions using existing typed child owners."""

import re
from collections.abc import Mapping

from ..ability_fragments import (
    GrantedActivatedAbilitySpec, GrantedTriggeredAbilitySpec, StaticComponentSpec,
    canonical_ability_fragments,
)
from ..fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD, TYPED_TOKEN_ABILITY_CAPABILITY_ID


def fixed_equipment_token_closure(effect):
    characteristics = effect.get('characteristics')
    if (not isinstance(characteristics, Mapping)
            or not {'type_line','colors',TOKEN_LITERAL_NAME_VERSION_FIELD} <= set(characteristics)
            or set(characteristics) - {'type_line','colors',TOKEN_LITERAL_NAME_VERSION_FIELD,'ability_fragments'}
            or type(characteristics[TOKEN_LITERAL_NAME_VERSION_FIELD]) is not int
            or characteristics[TOKEN_LITERAL_NAME_VERSION_FIELD] != 1
            or re.fullmatch(r'Token (?:Legendary )?Artifact — (?:Book )?Equipment', str(characteristics['type_line'])) is None
            or characteristics['colors'] not in ([],['W'],['U'],['B'],['R'],['G'])
            or not isinstance(effect.get('name'),str)
            or re.fullmatch(r"[A-Z][A-Za-z' -]*(?:, [A-Z][A-Za-z' -]*)?",effect['name']) is None):
        return None
    raw = characteristics.get('ability_fragments', [])
    if not isinstance(raw,list) or len(raw) > 4:
        return None
    try:
        fragments = canonical_ability_fragments(raw)
    except (TypeError,ValueError):
        return None
    if len(fragments) != len(raw):
        return None
    trigger_keys = {f.semantic_key for f in fragments if isinstance(f,GrantedTriggeredAbilitySpec)}
    for fragment in fragments:
        if isinstance(fragment,StaticComponentSpec):
            if (fragment.schema_version != 1 or (fragment.semantic_key not in trigger_keys
                    and re.fullmatch(r'.+:static:[^:]+:n\d+:token:[1-9]\d*',fragment.semantic_key) is None)):
                return None
        elif isinstance(fragment,(GrantedActivatedAbilitySpec,GrantedTriggeredAbilitySpec)):
            if ':token:' not in fragment.ability_id or not fragment.semantic_key.endswith(':'+fragment.ability_id):
                return None
        else:
            return None
    dependencies = ('token.creation.fixed_definition',)
    if fragments:
        dependencies += (TYPED_TOKEN_ABILITY_CAPABILITY_ID,'attachment.equip.fixed_mana')
    return dependencies, {'cr-111-tokens','fixed-named-token'}
