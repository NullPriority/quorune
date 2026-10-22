from __future__ import annotations

import re
from ..basic_land_types import BASIC_LAND_MANA
from ..basic_type_setting_model import BASIC_TYPE_SETTING_CAPABILITY,BASIC_TYPE_SETTING_HANDLER,BasicTypeSettingSpec


def fixed_basic_type_setting_handler(text):
    material=' '.join(text.strip().split())
    converted=re.fullmatch(r'Enchanted permanent is a colorless Forest land\.?',material,re.I)
    global_type=re.fullmatch(r'All (?P<from>Plains|Islands|Swamps|Mountains|Forests) are (?P<to>Plains|Islands|Swamps|Mountains|Forests)\.?',material,re.I)
    if converted is not None or global_type is not None:
        plural={'plains':'plains','islands':'island','swamps':'swamp','mountains':'mountain','forests':'forest'}
        spec=BasicTypeSettingSpec('attached_colorless_forest_permanent',('forest',)) if converted else BasicTypeSettingSpec(
            'all_'+global_type['from'].casefold(),(plural[global_type['to'].casefold()],))
        return ('fixed-basic-type-setting-v1',{'handler_id':BASIC_TYPE_SETTING_HANDLER,'schema_version':1,
            'event':'characteristics.evaluate','setting':spec.to_dict()},BASIC_TYPE_SETTING_CAPABILITY)
    match=re.fullmatch(r'(?P<subject>Nonbasic lands|Lands you control|Enchanted land) (?P<verb>are|is) (?P<types>.+?)\.?',material,re.I)
    if match is None:return None
    subject=match['subject'].casefold();attached=subject=='enchanted land'
    if match['verb'].casefold()!=('is' if attached else 'are'):return None
    words=match['types'].casefold()
    if attached:
        article=re.match(r'^(a|an) ',words)
        if article is None:return None
        body=words[article.end():]
        parts=re.findall(r'[a-z]+',body);parts=[p for p in parts if p!='and']
        if not parts or any(p not in BASIC_LAND_MANA for p in parts) or len(set(parts))!=len(parts):return None
        expected=parts[0] if len(parts)==1 else ', '.join(parts[:-1])+(', and ' if len(parts)>2 else ' and ')+parts[-1]
        if body!=expected:return None
        if article[1]!=('an' if parts[0][0] in 'aeiou' else 'a'):return None
        types=tuple(sorted(parts))
    else:
        plural={'plains':'plains','islands':'island','swamps':'swamp','mountains':'mountain','forests':'forest'}
        if words not in plural:return None
        types=(plural[words],)
    spec=BasicTypeSettingSpec({'nonbasic lands':'nonbasic_lands','lands you control':'controller_lands','enchanted land':'attached_land'}[subject],types)
    return ('fixed-basic-type-setting-v1',{'handler_id':BASIC_TYPE_SETTING_HANDLER,'schema_version':1,
        'event':'characteristics.evaluate','setting':spec.to_dict()},BASIC_TYPE_SETTING_CAPABILITY)
