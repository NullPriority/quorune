from __future__ import annotations

import re
from ..characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope as S,CharacteristicQuantityReduction as R
from ..object_predicate import ObjectQuerySpec


def public_zone_quantity(text):
    value=' '.join(text.strip().rstrip('.').split())
    value=re.sub(r'^(?:the )?(?:total )?number of ','',value,flags=re.I)
    maximum=re.fullmatch(r'cards in the hand of the opponent with the most cards in hand',value,re.I)
    if maximum:return CharacteristicQuantitySpec(S.OPPONENT_ZONES,ObjectQuerySpec(zones=('hand',)),schema_version=5,reduction=R.MAXIMUM_ZONE_SIZE)
    sizes=((r'cards? in your library',S.CONTROLLER_ZONE,'library'),
        (r"cards? in your opponents['’] hands",S.OPPONENT_ZONES,'hand'),
        (r"cards? in all players' hands",S.ALL_ZONES,'hand'))
    for pattern,scope,zone in sizes:
        if re.fullmatch(pattern,value,re.I):return CharacteristicQuantitySpec(scope,ObjectQuerySpec(zones=(zone,)),schema_version=5)
    owned=re.fullmatch(r'(?P<quality>cards?|instant and sorcery cards?|noncreature cards?|creature cards?|land cards?) you own in (?P<zones>exile and in your graveyard|exile)',value,re.I)
    if owned is None:return None
    quality=owned['quality'].casefold();fields={}
    if quality.startswith('instant'):fields['types_any']=('instant','sorcery')
    elif quality.startswith('noncreature'):fields['excluded_types']=('creature',)
    elif quality.startswith('creature'):fields['types_all']=('creature',)
    elif quality.startswith('land'):fields['types_all']=('land',)
    zones=('exile','graveyard') if 'graveyard' in owned['zones'].casefold() else ('exile',)
    return CharacteristicQuantitySpec(S.CONTROLLER_ZONE,ObjectQuerySpec(zones=zones,token=False,**fields),schema_version=5)
