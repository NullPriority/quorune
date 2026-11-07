from __future__ import annotations

"""Closed controller exile scans with their printed cast choices."""

import re

from ..discover_model import DISCOVER_OPERATION, NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template


_FIRST_NONLAND_CAST = re.compile(
    r'Exile cards from the top of your library until you exile a nonland card\. '
    r'You may cast (?:that card|it) without paying its mana cost\.?', re.I,
)


def discover_effect_template(text,*,source_name='',cast_x_available=False,forbid_public_x=False,**context):
    if _FIRST_NONLAND_CAST.fullmatch(text.strip()):
        return ('exile-until-nonland-cast-v1', ({'op': NONLAND_SCAN_OPERATION, 'schema_version': 1,
                'player': '$controller'},), None, (NONLAND_SCAN_MECHANIC,))

    def fixed(body):
        match=re.fullmatch(r'Discover (?P<amount>0|[1-9][0-9]*)\.?',body.strip(),re.I)
        if match is None:return None,(),None,()
        return 'discover-controller-v1',({'op':DISCOVER_OPERATION,'schema_version':1,
            'player':'$controller','amount':int(match['amount'])},),None,('discover',)
    result=fixed(text)
    if result[0] is not None:return result
    if not source_name:return None
    return declared_effect_amount_template(text,source_name=source_name,compile_fixed=fixed,
        cast_x_available=cast_x_available,forbid_public_x=forbid_public_x) or scalar_effect_amount_template(
            text,source_name=source_name,compile_fixed=fixed,event=context.get('event'),source_event=context.get('source_event',False))
