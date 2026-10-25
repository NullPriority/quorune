from __future__ import annotations

"""Whole-sentence grammar for one public recipient-local static scalar."""

import re
from ..recipient_modifier_model import RecipientQuantitySpec,RecipientQuantityKind,RECIPIENT_MODIFIER_CAPABILITY
from ..semantic_runtime.recipient_anthems import RecipientAnthemHandler,RECIPIENT_ANTHEM_HANDLER
from .public_state_queries import fixed_characteristic_battlefield_query_subject


def recipient_modifier_handler(text):
    material=' '.join(text.strip().split())
    label=re.fullmatch(r"(?P<label>[A-Z][A-Za-z0-9' ]{0,80}) — (?P<body>.+)",material)
    if label is not None:
        if label['label'].casefold().startswith(('if ','when ','whenever ','as ','at ','during ','this ','that ','you ','your ')):return None
        if re.fullmatch(r'max speed|boast|exhaust|forecast|level(?: up| \d+)?|station',label['label'],re.I):return None
        material=label['body']
    match=re.fullmatch(r'(?P<subject>.+?) gets? (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) for each '
        r'(?P<quantity>.+?)(?:, to a maximum of (?P<maximum>[1-9]\d*))?\.',material,re.I)
    if match is None:return None
    subject_text=match['subject']
    each=re.fullmatch(r"Each (?P<qualifiers>(?:[A-Za-z][A-Za-z'-]* )*)creature(?P<tail> .+)?",subject_text,re.I)
    if each is not None:subject_text=each['qualifiers']+'creatures'+(each['tail'] or '')
    subject=fixed_characteristic_battlefield_query_subject(subject_text)
    if subject is None or 'creature' not in subject[1].types_all:return None
    phrase=match['quantity'].casefold()
    if phrase=='of its colors':spec=RecipientQuantitySpec(RecipientQuantityKind.COLORS)
    elif phrase=='of its creature types':spec=RecipientQuantitySpec(RecipientQuantityKind.CREATURE_TYPES)
    elif phrase=='+1/+1 counter on it':spec=RecipientQuantitySpec(RecipientQuantityKind.COUNTERS,counter='+1/+1')
    else:
        mana=re.fullmatch(r'(?P<color>white|blue|black|red|green) mana symbol in its mana cost',phrase)
        if mana is None:return None
        spec=RecipientQuantitySpec(RecipientQuantityKind.MANA_SYMBOLS,mana_color={'white':'W','blue':'U','black':'B','red':'R','green':'G'}[mana['color']])
    if match['maximum'] is not None:
        spec=RecipientQuantitySpec(spec.kind,counter=spec.counter,mana_color=spec.mana_color,maximum=int(match['maximum']))
    relation,predicate,exclude=subject
    descriptor={'handler_id':RECIPIENT_ANTHEM_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'condition':{'target_controller':relation,'predicate':predicate.to_dict(),'exclude_source':exclude},
        'modifier':{'schema_version':1,'quantity':spec.to_dict(),'power':int(match['power']),'toughness':int(match['toughness'])}}
    try:RecipientAnthemHandler().validate(descriptor)
    except (TypeError,ValueError):return None
    return 'continuous-recipient-quantity-anthem-v1',(descriptor,),('continuous.power_toughness.fixed_anthem',RECIPIENT_MODIFIER_CAPABILITY)
