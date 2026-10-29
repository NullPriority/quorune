from __future__ import annotations

"""Whole source-pinned static clauses over the enchanted player's creatures."""

import re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE,ability_fragment_to_dict
from ..enchanted_player_model import (EnchantedPlayerAttackRequirementSpec,ENCHANTED_PLAYER_ATTACK_CAPABILITY,
    ENCHANTED_PLAYER_ATTACK_HANDLER,ENCHANTED_PLAYER_CHARACTERISTICS_CAPABILITY,ENCHANTED_PLAYER_CHARACTERISTICS_HANDLER)
from .static_runtime_nodes import runtime_handler_node


def enchanted_player_static_node(**values):
    match=re.fullmatch(r'(?P<nonlegendary>Nonlegendary )?creatures enchanted player controls (?P<body>.+?)\.?',
        ' '.join(values['material_line'].strip().split()),re.I)
    if match is None:return None
    body=match['body'];nonlegendary=match['nonlegendary'] is not None
    if re.fullmatch(r'attack each combat if able',body,re.I):
        if nonlegendary:return None
        descriptor={'handler_id':ENCHANTED_PLAYER_ATTACK_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
            'fragment':ability_fragment_to_dict(EnchantedPlayerAttackRequirementSpec())}
        capabilities=(ENCHANTED_PLAYER_ATTACK_CAPABILITY,'combat.declaration.typed_components')
        coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,)
    else:
        from .continuous_templates import _attached_modifier
        from .fixed_characteristic_settings import _setting_part,SETTING_CAPABILITY
        from ..creature_subtypes import CREATURE_SUBTYPES
        from ..semantic_runtime.enchanted_player_characteristics import EnchantedPlayerCharacteristicsHandler
        modifier=_attached_modifier()
        parts=re.split(r',\s*(?:and )?| and (?=(?:are|is|have|has|lose|loses|get|gets)\b)',body,flags=re.I)
        removes=[part for part in parts if re.fullmatch(r'(?:and )?(?:lose|loses) all(?: other)? abilities',part.strip(),re.I)]
        if len(removes)>1 or (removes and re.search(r'\b(?:have|has) (?!base )',body,re.I)
                and not re.search(r'\b(?:lose|loses) all other abilities\b',body,re.I)):return None
        for part in parts:
            if part.casefold()=='lose all creature types':
                modifier['type_operations'].append({'op':'remove_types','field':'subtypes','values':sorted(s.title() for s in CREATURE_SUBTYPES)})
            elif not _setting_part(modifier,part):return None
        descriptor={'handler_id':ENCHANTED_PLAYER_CHARACTERISTICS_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
            'target':{'nonlegendary':nonlegendary},'modifier':modifier}
        try:EnchantedPlayerCharacteristicsHandler().validate(descriptor)
        except (TypeError,ValueError):return None
        capabilities=(ENCHANTED_PLAYER_CHARACTERISTICS_CAPABILITY,SETTING_CAPABILITY)
        coverage=()
    return runtime_handler_node(node_id=values['node_id'],line=values['line'],span=values['span'],
        compiled=('static-enchanted-player-scope-v1',descriptor,capabilities),kind='static_ability',event='characteristics.evaluate',
        runtime_coverage=coverage,dependency_reason='Enchanted-player static scope requires its independently closed rule owner',
        capability_registry=values['capability_registry'],capability_profile=values['capability_profile'],residuals=values['residuals'])
