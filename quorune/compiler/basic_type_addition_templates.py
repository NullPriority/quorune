from __future__ import annotations

import re
from ..basic_land_types import BASIC_LAND_MANA
from ..basic_type_addition_model import BASIC_TYPE_ADDITION_CAPABILITY,BASIC_TYPE_ADDITION_HANDLER,BasicTypeAdditionSpec


def scoped_basic_type_addition_handler(text):
    material=' '.join(text.strip().split())
    match=re.fullmatch(r'(?P<subject>All lands|Lands you control|Enchanted land) '
        r'(?P<verb>are|is) (?P<types>.+?) in addition to (?P<pronoun>their|its) other (?:land )?types\.?',material,re.I)
    if match is None:return None
    subject=match['subject'].casefold()
    attached=subject=='enchanted land'
    agreement=('is','its') if attached else ('are','their')
    if (match['verb'].casefold(),match['pronoun'].casefold())!=agreement:
        return None
    words=match['types'].casefold()
    if words=='every basic land type':types=tuple(sorted(BASIC_LAND_MANA))
    else:
        plural={'plains':'plains','islands':'island','swamps':'swamp','mountains':'mountain','forests':'forest'}
        if attached:
            words=re.sub(r'^(?:a|an) ','',words)
            if words not in BASIC_LAND_MANA:return None
            types=(words,)
        elif words in plural:types=(plural[words],)
        else:return None
    scope={'all lands':'all_lands','lands you control':'controller_lands','enchanted land':'attached_land'}[subject]
    spec=BasicTypeAdditionSpec(scope,types)
    return ('scoped-basic-type-addition-v1',{'handler_id':BASIC_TYPE_ADDITION_HANDLER,
        'schema_version':1,'event':'characteristics.evaluate','addition':spec.to_dict()},BASIC_TYPE_ADDITION_CAPABILITY)
