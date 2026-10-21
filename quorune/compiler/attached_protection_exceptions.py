from __future__ import annotations

"""Closed fixed-color grants with one source-Aura retention exception."""

from dataclasses import replace
import re
from ..ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict,ProtectionSpec
from ..protection_attachment_exceptions import CAPABILITY,SOURCE_AURA


def attached_protection_exception_handler(line,*,source_name='source'):
    match=re.fullmatch(r"Enchanted creature has protection from (white|blue|black|red|green)\. "
                      r"This effect doesn't remove this Aura\.",line.strip(),re.IGNORECASE)
    if match is None:return None
    from .continuous_templates import attached_fixed_characteristics_handler
    compiled=attached_fixed_characteristics_handler('Enchanted creature has protection from '+match.group(1)+'.',source_name=source_name)
    if compiled is None:return None
    _,handler,caps=compiled;modifier=dict(handler['modifier']);fragments=[]
    for raw in modifier['add_ability_fragments']:
        spec=ability_fragment_from_dict(raw)
        if not isinstance(spec,ProtectionSpec):return None
        fragments.append(ability_fragment_to_dict(replace(spec,schema_version=3,exempt_aura_incarnation=SOURCE_AURA)))
    if len(fragments)!=1:return None
    return ('continuous-attached-source-aura-protection-exception-v1',
            {**handler,'modifier':{**modifier,'add_ability_fragments':fragments}},tuple(sorted({*caps,CAPABILITY})))
