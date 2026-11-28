from __future__ import annotations

"""Closed controller exile scans with their printed cast choices."""

from . import regex_working_set as re

from ..discover_model import (DISCOVER_OPERATION, DISCOVER_TREASURE_DIFFERENCE_VERSION,
    NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION,
    QUALIFIED_SCAN_MECHANIC, QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_TARGET)
from ..replacement.immutable import thaw_value
from ..fixed_top_exile import (
    CHOSEN_IMPULSE_MECHANIC, CHOSEN_IMPULSE_SCOPE, FIXED_TOP_EXILE_MECHANIC, FIXED_TOP_EXILE_OPERATION,
    RESOLVING_TOP_PLAY_MECHANIC, RESOLVING_TOP_PLAY_SCOPE, opponent_chosen_impulse_instruction,
)
from ..impulse_access_model import ImpulseAccessDuration
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..rules.controller_private_top_exile_model import (
    CONTROLLER_PRIVATE_TOP_EXILE_MECHANIC, ControllerPrivateTopExile,
)
from ..creature_subtypes import canonical_creature_subtype
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template


_DISCOVER_TREASURE_DIFFERENCE = re.compile(
    r"Discover (?P<amount>[1-9][0-9]*)\. If the discovered card['’]s mana value is less than "
    r"(?P=amount), create a number of tapped Treasure tokens equal to the difference\.?", re.I,
)


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


_CHOSEN_IMPULSE = re.compile(
    rf"Exile the top (?P<count>{FIXED_COUNT_PATTERN}) cards of your library(?:\. (?:Then )?|, then )choose one of them\. "
    r"(?:(?P<prefix>Until (?:the )?end of (?:your next turn|this turn|turn)), you may play (?:that card|it)|"
    r"You may play (?:that card|it) (?P<suffix>this turn|until (?:the )?end of your next turn))\.?", re.I,
)


def _chosen_impulse_template(text):
    match = _CHOSEN_IMPULSE.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    count = fixed_number(match["count"])
    if not 2 <= count <= 10:
        return None
    window = (match["prefix"] or match["suffix"]).casefold()
    duration = ImpulseAccessDuration.END_OF_NEXT_TURN if "next turn" in window else ImpulseAccessDuration.END_OF_TURN
    effect = {"op": FIXED_TOP_EXILE_OPERATION, "schema_version": 2, "player": "$controller",
              "scope": CHOSEN_IMPULSE_SCOPE, "count": count, "duration": duration.value}
    return "chosen-top-impulse-access-v1", (effect,), None, (CHOSEN_IMPULSE_MECHANIC,)


_TARGETED_CHOSEN_IMPULSE = re.compile(
    rf"Exile the top (?P<count>{FIXED_COUNT_PATTERN}) cards of target opponent['’]s library\. "
    r"Choose one of them\. Until the end of your next turn, you may play that card, "
    r"and you may spend mana as though it were mana of any color to cast it\.?", re.I,
)


def _fixed_top_exile_template(text, *, source_name, event):
    body = text.strip()
    if chosen := _chosen_impulse_template(body):
        return chosen
    if match := _TARGETED_CHOSEN_IMPULSE.fullmatch(" ".join(body.split())):
        count = fixed_number(match["count"])
        if 2 <= count <= 10:
            return ("opponent-chosen-top-impulse-v1", (opponent_chosen_impulse_instruction(count),),
                    thaw_value(QUALIFIED_SCAN_TARGET), (CHOSEN_IMPULSE_MECHANIC,))
    if re.fullmatch(r"Exile the top two cards of your library\. You may play them\.?", body, re.I):
        effect = {"op": FIXED_TOP_EXILE_OPERATION, "schema_version": 3, "player": "$controller",
                  "scope": RESOLVING_TOP_PLAY_SCOPE, "count": 2}
        return "fixed-top-exile-resolving-play-v1", (effect,), None, (RESOLVING_TOP_PLAY_MECHANIC,)
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


_CONTROLLER_PRIVATE_TOP_PLAY = re.compile(
    r"Look at the top two cards of your library and exile them face down\. "
    r"For as long as they remain exiled, you may play them if you control an? (?P<subtype>[A-Za-z'’-]+)\.",
    re.I,
)


def controller_private_top_play_template(text):
    match = _CONTROLLER_PRIVATE_TOP_PLAY.fullmatch(" ".join(text.split()))
    subtype = canonical_creature_subtype(match["subtype"]) if match else None
    if subtype is None:
        return None
    return ("controller-private-top-conditional-play-v1", (ControllerPrivateTopExile(subtype).instruction(),),
            None, (CONTROLLER_PRIVATE_TOP_EXILE_MECHANIC,))


def discover_effect_template(text,*,source_name='',cast_x_available=False,forbid_public_x=False,**context):
    if context.get("source_is_permanent") is False:
        difference = _DISCOVER_TREASURE_DIFFERENCE.fullmatch(' '.join(text.split()))
        if difference is not None:
            return ('discover-tapped-treasure-difference-v1', ({'op': DISCOVER_OPERATION,
                'schema_version': DISCOVER_TREASURE_DIFFERENCE_VERSION, 'player': '$controller',
                'amount': int(difference['amount'])},), None, ('discover',))
        private_play = controller_private_top_play_template(text)
        if private_play is not None:
            return private_play
    total_mana = _total_mana_exile_template(text)
    if total_mana is not None:return total_mana
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


def elected_nonland_exile_trigger_result(binding, text):
    if binding.variant != "your upkeep" or binding.active_zone != "battlefield" or text != binding.body:
        return None
    return _elected_nonland_exile_template(text, event=binding.event.value)


def _elected_nonland_exile_template(text, *, event):
    if event != "step.begin" or re.fullmatch(
        r"Each player exiles cards from the top of their library until they exile a nonland card\. "
        r"An opponent chooses a nonland card exiled this way\. "
        r"You may cast up to two spells from among the other cards exiled this way without paying their mana costs\.?",
        " ".join(text.split()), re.I,
    ) is None:
        return None
    from ..rules.elected_nonland_exile import ELECTED_NONLAND_EXILE_MECHANIC, elected_nonland_exile_instruction

    return ("each-library-nonland-election-free-casts-v1", (elected_nonland_exile_instruction(),),
            None, (FIXED_TOP_EXILE_MECHANIC, ELECTED_NONLAND_EXILE_MECHANIC))


def _total_mana_exile_template(text):
    match=re.fullmatch(r"Exile cards from the top of your library until you exile cards with total mana value "
        r"(?P<amount>[1-9][0-9]*) or greater\. You may cast any number of spells from among them without paying their mana costs\.?",
        " ".join(text.strip().split()),re.I)
    if match is None:return None
    from ..mana_value_exile_model import TOTAL_MANA_EXILE_SCOPE,TOTAL_MANA_EXILE_MECHANIC
    return ('total-mana-value-exile-cast-subset-v1',({'op':FIXED_TOP_EXILE_OPERATION,'schema_version':4,
        'scope':TOTAL_MANA_EXILE_SCOPE,'player':'$controller','minimum_total':int(match['amount'])},),None,
        (FIXED_TOP_EXILE_MECHANIC,TOTAL_MANA_EXILE_MECHANIC))
