from __future__ import annotations

import re
from ..rules.global_alternative_cost_model import (
    GLOBAL_ALTERNATIVE_CAPABILITY,GLOBAL_ALTERNATIVE_HANDLER,GLOBAL_ALTERNATIVE_EVENT,GlobalAlternativeCostSpec,
)
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..util import mana_cost_to_vector
from .creature_subtypes import canonical_creature_subtype


def fixed_global_alternative_cost_handler(text):
    material=' '.join(text.strip().split())
    paid=re.fullmatch(r'You may pay (?P<mana>(?:\{(?:[0-9]+|[WUBRGC])\})+) rather than pay the mana cost for (?P<subject>.+?) spells you cast\.?',material,re.I)
    any_paid=re.fullmatch(r'You may pay (?P<mana>(?:\{(?:[0-9]+|[WUBRGC])\})+) rather than pay the mana cost for spells you cast\.?',material,re.I)
    free=re.fullmatch(r'You may cast (?P<subject>spells from your hand|[A-Z][A-Za-z\x27-]* spells) without paying their mana costs\.?',material,re.I)
    predicate=ObjectQuerySpec();origin='any';without=False
    if paid or any_paid:
        match=paid or any_paid;mana,complex_symbols=mana_cost_to_vector(match['mana'])
        if complex_symbols:return None
        if paid:
            subject=paid['subject']
            creature=subject.casefold().endswith(' creature')
            subtype=canonical_creature_subtype(subject[:-9] if creature else subject)
            if subtype is None:return None
            predicate=ObjectQuerySpec(types_all=('creature',) if creature else (),subtypes_all=(subtype,))
    elif free:
        mana,_=mana_cost_to_vector('{0}');without=True
        if free['subject'].casefold()=='spells from your hand':origin='hand'
        else:
            subtype=canonical_creature_subtype(free['subject'][:-7])
            if subtype is None:return None
            predicate=ObjectQuerySpec(subtypes_all=(subtype,))
    else:return None
    spec=GlobalAlternativeCostSpec(FrozenMap(mana),predicate,origin,without)
    return ('fixed-battlefield-alternative-cost-v1',{'handler_id':GLOBAL_ALTERNATIVE_HANDLER,'schema_version':1,
        'event':GLOBAL_ALTERNATIVE_EVENT,'requires_complete_card_program':True,'grant':spec.to_dict()},GLOBAL_ALTERNATIVE_CAPABILITY)
