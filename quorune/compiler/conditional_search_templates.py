"""Revealed found-card placement after a single closed library search."""
import re
from ..conditional_search_model import ConditionalSearchSpec,CONDITIONAL_SEARCH_MECHANIC
from ..library_search_model import FixedCountedLibrarySearchTemplate,search_selector,FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID
from ..replacement.immutable import FrozenMap
from .library_search_templates import _search_query
from .public_state_queries import fixed_public_state_condition


def conditional_search_effect_template(text,*,source_name):
    head=re.fullmatch(r'Search your library for (?:a|an) (?P<quality>.+?) card and reveal it\. (?P<tail>.+)',text.strip(),re.I)
    if head is None:return None
    query=_search_query(head['quality'],destination='hand')
    if query is None:return None
    placement=None;tail=head['tail']
    public=re.fullmatch(r'If (?P<condition>[^.]+?), put it onto the battlefield(?P<tapped> tapped)?\. Otherwise, put it into your hand\. Then shuffle\.',tail,re.I)
    amount=re.fullmatch(r'Put it onto the battlefield(?P<tapped> tapped)? if its mana value is (?P<amount>[0-9]+) or less\. Otherwise, put it into your hand\. (?:Then shuffle|If you search your library this way, shuffle)\.',tail,re.I)
    card_type=re.fullmatch(r"Put it onto the battlefield(?P<tapped> tapped)? if it['’]s a (?P<type>land|creature|artifact|enchantment|battle|planeswalker) card\. Otherwise, put it into your hand\. Then shuffle\.",tail,re.I)
    if public is not None:
        condition=fixed_public_state_condition(public['condition'],source_name=source_name)
        if condition is None:return None
        placement={'kind':'public_state','condition':condition.to_dict(),'enters_tapped':bool(public['tapped'])}
    elif amount is not None:placement={'kind':'mana_value_at_most','maximum':int(amount['amount']),'enters_tapped':bool(amount['tapped'])}
    elif card_type is not None:placement={'kind':'card_type','card_type':card_type['type'].casefold(),'enters_tapped':bool(card_type['tapped'])}
    if placement is None:return None
    try:
        spec=ConditionalSearchSpec(FixedCountedLibrarySearchTemplate(1,False,FrozenMap(search_selector(query)),'hand',True),FrozenMap(placement))
    except ValueError:return None
    return ('conditional-search-placement-v1',(spec.effect(),),None,(FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,CONDITIONAL_SEARCH_MECHANIC))
