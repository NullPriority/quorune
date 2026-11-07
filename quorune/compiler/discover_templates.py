from __future__ import annotations

"""Closed controller exile scans with their printed cast choices."""

import re

from ..discover_model import (DISCOVER_OPERATION, NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION,
    QUALIFIED_SCAN_MECHANIC, QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_TARGET)
from ..replacement.immutable import thaw_value
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template


_FIRST_NONLAND_CAST = re.compile(
    r'Exile cards from the top of your library until you exile a nonland card\. '
    r'You may cast (?:that card|it) without paying its mana cost\.?', re.I,
)
_QUALIFIED_SCAN = (
    r'exiles cards from the top of their library until they exile an instant or sorcery card\. '
    r'You may cast that card without paying its mana cost\. '
)
_TARGETED_QUALIFIED_SCAN = re.compile(
    r'Target opponent ' + _QUALIFIED_SCAN +
    r'Then put the exiled cards that weren[’\']t cast this way on the bottom of that library in a random order\.?', re.I,
)
_DAMAGE_QUALIFIED_SCAN = re.compile(
    r'That player ' + _QUALIFIED_SCAN +
    r'Then that player puts the exiled cards that weren[’\']t cast this way on the bottom of their library in a random order\.?', re.I,
)


def _qualified_exile_cast(supplier, target_schema):
    return ('exile-until-instant-or-sorcery-cast-v1',
            ({'op': QUALIFIED_SCAN_OPERATION, 'schema_version': 1,
              'player': '$controller', 'supplier': supplier},),
            target_schema, (QUALIFIED_SCAN_MECHANIC,))


def damage_player_qualified_exile_cast(binding, body):
    from .event_player_results import _event_player
    if binding.variant not in {'source_combat_damage_player', 'public_deals_combat_damage_to_a_player'}:
        return None
    player = _event_player(binding, 'that player')
    if player is not None and _DAMAGE_QUALIFIED_SCAN.fullmatch(body.strip()):
        return _qualified_exile_cast(player, None)
    return None


def discover_effect_template(text,*,source_name='',cast_x_available=False,forbid_public_x=False,**context):
    if _TARGETED_QUALIFIED_SCAN.fullmatch(text.strip()):
        return _qualified_exile_cast('$target.0', thaw_value(QUALIFIED_SCAN_TARGET))
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
