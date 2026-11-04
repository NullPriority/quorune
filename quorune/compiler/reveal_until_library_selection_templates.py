from __future__ import annotations

"""Closed controller reveal-until instructions with independently owned results."""

import re

from ..object_predicate import ObjectQuerySpec
from ..reveal_until_library_selection_model import (
    REVEAL_UNTIL_OPERATION,REVEAL_UNTIL_MECHANIC,validate_reveal_until_selection,
)
from .fixed_library_selection_templates import _term_predicates
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .oracle_source_text import without_ability_word_label

_HEADER = re.compile(r'Reveal cards from the top of your library until you reveal '
    rf'(?P<count>a|an|{FIXED_COUNT_PATTERN}) (?P<kind>[^.,]+?) cards?(?P<separator>\. |, then )(?P<tail>.+)',re.I)
_ONE_RESULT = re.compile(r'Put that card (?P<destination>into your hand|onto the battlefield(?P<tapped> tapped)?) '
    r'and (?P<remainder>the rest|all other cards revealed this way) '
    r'(?P<rest>into your graveyard|on the bottom of your library in (?P<order>a random|any) order)\.',re.I)


def reveal_until_library_selection_effect_template(text):
    if 'until' not in text.casefold() or 'reveal' not in text.casefold():return None
    material = without_ability_word_label(' '.join(text.strip().split()))
    match = _HEADER.fullmatch(material) if material is not None else None
    if match is None:return None
    count = 1 if match['count'].casefold() in {'a','an'} else fixed_number(match['count'])
    kind = match['kind'].casefold()
    predicates = _term_predicates(kind)
    if kind == 'nonlegendary creature':
        predicates = (ObjectQuerySpec(zones=('library',),types_any=('creature',),
            excluded_supertypes=('legendary',)).to_dict(),)
    if not predicates:return None
    effect = {'op':REVEAL_UNTIL_OPERATION,'schema_version':1,'player':'$controller',
        'stop_count':count,'predicate_groups':[list(predicates)],'selection':'matching',
        'selected_destination':'hand','entry_tapped':False,
        'remainder_destination':'graveyard','remainder_order':'chosen'}
    tail = match['tail']
    if re.fullmatch(r'put all cards revealed this way into your hand\.',tail,re.I):
        effect['selection'] = 'all_revealed'
    else:
        result = _ONE_RESULT.fullmatch(tail)
        all_matching = re.fullmatch(r'Put the (?P<kind>.+?) cards revealed this way into your hand, '
            r'then put the rest of the revealed cards on the bottom of your library in any order\.',tail,re.I)
        if result is not None and count == 1:
            if result['destination'].casefold().startswith('onto'):
                effect.update(selected_destination='battlefield',entry_tapped=bool(result['tapped']))
            if result['rest'].casefold().startswith('on the bottom'):
                effect.update(remainder_destination='library_bottom',
                    remainder_order='random' if result['order'].casefold()=='a random' else 'chosen')
        elif all_matching is not None and all_matching['kind'].casefold() == kind:
            effect.update(remainder_destination='library_bottom',remainder_order='chosen')
        else:return None
    try:validate_reveal_until_selection(effect)
    except (TypeError,ValueError,KeyError):return None
    return 'reveal-until-library-selection-v1',(effect,),None,(REVEAL_UNTIL_MECHANIC,)
