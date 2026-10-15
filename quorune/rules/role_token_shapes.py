from __future__ import annotations

"""Closed predefined Role creation and explicit token child bindings."""

import re
from collections.abc import Mapping

from ..ability_fragments import GrantedTriggeredAbilitySpec, StaticComponentSpec, canonical_ability_fragments
from ..enchant_spec import SimpleEnchantSpec
from ..targets import TargetGroup

ROLE_TOKEN_CAPABILITY = 'token.creation.predefined_role'


def role_token_node_capabilities(*, effects, target_schema, mechanics):
    if 'predefined-role-token' not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (not isinstance(effect, Mapping) or set(effect) != {'op', 'controller', 'name', 'quantity', 'aura_target_ref', 'characteristics'}
            or effect['op'] != 'create_role_token' or effect['controller'] != '$controller'
            or effect['name'] not in {'Cursed', 'Monster', 'Royal', 'Sorcerer', 'Virtuous', 'Wicked', 'Young Hero'}
            or type(effect['quantity']) is not int or effect['quantity'] != 1):
        return ()
    characteristics = effect['characteristics']
    if (not isinstance(characteristics, Mapping) or set(characteristics) != {'type_line', 'colors', 'ability_fragments'}
            or characteristics['type_line'] != 'Token Enchantment — Aura Role' or characteristics['colors'] != []):
        return ()
    try:
        fragments = canonical_ability_fragments(characteristics['ability_fragments'])
    except (TypeError, ValueError):
        return ()
    if not fragments or sum(isinstance(f, SimpleEnchantSpec) and f.object_kind == 'creature' for f in fragments) != 1:
        return ()
    for fragment in fragments:
        if isinstance(fragment, SimpleEnchantSpec):
            continue
        if isinstance(fragment, StaticComponentSpec):
            if re.fullmatch(r'.+:static:[^:]+:n[0-9]+:role:[1-9][0-9]*', fragment.semantic_key) is None:
                return ()
        elif isinstance(fragment, GrantedTriggeredAbilitySpec):
            if ':token:' not in fragment.ability_id or not fragment.semantic_key.endswith(':' + fragment.ability_id):
                return ()
        else:
            return ()
    if target_schema is None:
        if effect['aura_target_ref'] != '$source.zone_object':
            return ()
    else:
        try:
            group = TargetGroup.from_mapping(target_schema)
        except (TypeError, ValueError):
            return ()
        if (effect['aura_target_ref'] != '$target.0' or group.zones != ('battlefield',)
                or group.categories != ('permanent',) or group.types_all != ('creature',)
                or group.min_targets not in {0, 1} or group.max_targets != 1):
            return ()
    return (ROLE_TOKEN_CAPABILITY, 'attachment.aura.simple_object', 'state_based.role_attachment.uniqueness')
