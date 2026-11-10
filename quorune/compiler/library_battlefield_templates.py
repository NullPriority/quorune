from __future__ import annotations

"""Fixed self-library inspection whose selected permanents use owned entry."""

import re

from ..mana_value_limit import ManaValueLimitSpec
from ..library_battlefield_model import (
    LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION, LIBRARY_SHUFFLE_REMAINDER, validate_library_battlefield_effect,
)
from .fixed_library_selection_templates import fixed_library_selection_effect_template
from .oracle_source_text import without_ability_word_label


def library_battlefield_selection_effect_template(text):
    material=without_ability_word_label(' '.join(text.strip().split()))
    if material is None or re.search(r'\btapped and attacking\b',material,re.I):return None
    if followup := _revealed_creature_entry_followup(material):
        return followup
    destinations=list(re.finditer(r'onto the battlefield(?P<tapped> tapped)?',material,re.I))
    if len(destinations)!=1:return None
    destination=destinations[0]
    limits=list(re.finditer(r' with mana value (?P<limit>0|[1-9][0-9]*) or less',material,re.I))
    if len(limits)>1:return None
    limit=ManaValueLimitSpec(int(limits[0]['limit'])) if limits else None
    if limits and (limits[0].start()<material.find('.') or limits[0].end()>destination.start()):return None
    proxy=re.sub(r' with mana value (?:0|[1-9][0-9]*) or less','',material,flags=re.I)
    proxy=re.sub(r'onto the battlefield(?: tapped)?','into your hand',proxy,flags=re.I)
    proxy=re.sub(r'Put all (.+?) cards from among them into your hand',
        r'Put all \1 cards revealed this way into your hand',proxy,flags=re.I)
    fixed=fixed_library_selection_effect_template(proxy)
    if fixed is None:return None
    effect={**fixed.compiled()[1][0],'schema_version':3,'selected_destination':'battlefield',
        'entry_tapped':bool(destination['tapped']),'mana_value_limit':limit.to_dict() if limit else None}
    try:validate_library_battlefield_effect(effect)
    except (TypeError,ValueError,KeyError):return None
    return 'library-battlefield-selection-v3',(effect,),None,('fixed-library-selection',)


def _revealed_creature_entry_followup(text: str):
    match = re.fullmatch(
        r"(?P<reveal>Reveal the top ten cards of your library\.) "
        r"Put a creature card from among them onto the battlefield with three \+1/\+1 counters on it\. "
        r"It gains hexproof until your next turn\. Then shuffle\.?", text, re.I,
    )
    if match is None:
        return None
    selection = fixed_library_selection_effect_template(
        f"{match['reveal']} Put a creature card from among them into your hand. "
        "Put the rest on the bottom of your library in a random order."
    )
    if selection is None:
        return None
    _, (base,), _, mechanics = selection.compiled()
    effect = {**base, "schema_version": LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION, "selected_destination": "battlefield",
              "entry_tapped": False, "mana_value_limit": None, "remainder_destination": LIBRARY_SHUFFLE_REMAINDER,
              "entry_followup": {"counters": 3}}
    validate_library_battlefield_effect(effect)
    return "library-revealed-creature-entry-followup-v4", (effect,), None, mechanics
