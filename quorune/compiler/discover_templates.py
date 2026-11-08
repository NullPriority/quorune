from __future__ import annotations

"""Closed controller exile scans with their printed cast choices."""

import re

from ..discover_model import (DISCOVER_OPERATION, NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION,
    QUALIFIED_SCAN_MECHANIC, QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_TARGET)
from ..replacement.immutable import thaw_value
from ..fixed_top_exile import FIXED_TOP_EXILE_MECHANIC, FIXED_TOP_EXILE_OPERATION
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
_EACH_TOP_CAST = re.compile(
    r"Exile the top card of each player's library,? then you may cast any number of spells "
    r"from among those cards without paying their mana costs\.?", re.I,
)
_EVENT_TOP_CAST = re.compile(
    r"That player exiles the top card of their library\. If it['’]s a nonland card, "
    r"you may cast it without paying its mana cost\.?", re.I,
)
_PAID_TOP_CAST = re.compile(
    r"Exile the top card of your library\. You may cast that card\. If you don['’]t, (?P<damage>.+)", re.I,
)


def _fixed_top_exile_template(text, *, source_name, event):
    body = text.strip()
    supplier = decline = None
    if _EACH_TOP_CAST.fullmatch(body):
        scope = "each_library"
    elif event == "spell.cast" and _EVENT_TOP_CAST.fullmatch(body):
        scope = "cast_event_library"
        supplier = "$context.player"
    else:
        match = _PAID_TOP_CAST.fullmatch(body)
        if match is None or not source_name:
            return None
        from .damage_templates import fixed_damage_effect_template
        damage = fixed_damage_effect_template(match["damage"], card_name=source_name)
        if damage is None:
            return None
        _, effects, schema, _ = damage.compiled()
        if schema is not None or len(effects) != 1 or effects[0].get("op") != "damage_each_opponent":
            return None
        decline = effects[0]
        scope = "controller_library"
    return ("fixed-top-exile-resolving-cast-v1", ({"op": FIXED_TOP_EXILE_OPERATION, "schema_version": 1,
            "player": "$controller", "scope": scope, "supplier": supplier, "decline": decline},),
            None, (FIXED_TOP_EXILE_MECHANIC,))


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


def cast_event_fixed_top_exile(binding, body):
    if binding.event.value == "spell.cast" and _EVENT_TOP_CAST.fullmatch(body.strip()):
        return _fixed_top_exile_template(body, source_name="", event="spell.cast")
    return None


def discover_effect_template(text,*,source_name='',cast_x_available=False,forbid_public_x=False,**context):
    fixed_top = _fixed_top_exile_template(text, source_name=source_name, event=context.get("event"))
    if fixed_top is not None:
        return fixed_top
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
