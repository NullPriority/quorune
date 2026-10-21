from __future__ import annotations

"""Closed color and enchantment-type grants retaining one source Aura."""

from dataclasses import replace
import re
from ..ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict,ProtectionSpec
from ..protection_attachment_exceptions import CAPABILITY,SOURCE_AURA,CHOSEN_AURA_PROTECTION_CAPABILITY,CHOSEN_AURA_PROTECTION_HANDLER,FIXED_TYPE_AURA_PROTECTION_CAPABILITY


def attached_protection_exception_handler(line,*,source_name='source'):
    if line.strip().casefold()=="enchanted creature has protection from the chosen color. this effect doesn't remove this aura.":
        return ('continuous-attached-source-chosen-color-protection-v1',
                {'handler_id':CHOSEN_AURA_PROTECTION_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
                 'subject':'source_attached_creature'},(CHOSEN_AURA_PROTECTION_CAPABILITY,CAPABILITY))
    match=re.fullmatch(r"Enchanted creature has protection from (white|blue|black|red|green)\. "
                      r"This effect doesn't remove this Aura\.",line.strip(),re.IGNORECASE)
    type_match=re.fullmatch(r"(?P<body>Enchanted creature (?:gets [+-]\d+/[+-]\d+ and has|has) protection from enchantments)\. "
                           r"This effect doesn't remove this Aura\.",line.strip(),re.IGNORECASE)
    if match is None and type_match is None:return None
    from .continuous_templates import attached_fixed_characteristics_handler
    body=type_match.group('body')+'.' if type_match is not None else 'Enchanted creature has protection from '+match.group(1)+'.'
    compiled=attached_fixed_characteristics_handler(body,source_name=source_name)
    if compiled is None:return None
    _,handler,caps=compiled;modifier=dict(handler['modifier']);fragments=[]
    for raw in modifier['add_ability_fragments']:
        spec=ability_fragment_from_dict(raw)
        if not isinstance(spec,ProtectionSpec):return None
        fragments.append(ability_fragment_to_dict(replace(spec,schema_version=3,exempt_aura_incarnation=SOURCE_AURA)))
    if len(fragments)!=1:return None
    dependencies={*caps,CAPABILITY}
    if type_match is not None:dependencies.add(FIXED_TYPE_AURA_PROTECTION_CAPABILITY)
    return ('continuous-attached-source-aura-protection-exception-v1',
            {**handler,'modifier':{**modifier,'add_ability_fragments':fragments}},tuple(sorted(dependencies)))
