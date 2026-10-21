from __future__ import annotations

"""Closed fixed-count random graveyard leaves; declared X reuses fixed probes."""

import re
from ..creature_subtypes import canonical_creature_subtype
from ..rules.random_graveyard_returns import OPERATION,MECHANIC,RandomGraveyardReturnSpec


def random_graveyard_return_template(text):
    match=re.fullmatch(r'Return (?P<count>an?|one|two|three|four|\d+) (?P<subject>.+?) at random from your graveyard to your hand\.?',text.strip(),re.I)
    if match is None:return None
    count={'a':1,'an':1,'one':1,'two':2,'three':3,'four':4}.get(match['count'].casefold())
    if count is None:count=int(match['count'])
    subject=match['subject'].casefold();subject=re.sub(r'\bcards\b','card',subject)
    try:selection=RandomGraveyardReturnSpec(subject)
    except ValueError:
        subtype=canonical_creature_subtype(subject.removesuffix(' card')) if subject.endswith(' card') else None
        if subtype is None:return None
        selection=RandomGraveyardReturnSpec('creature card',subtype)
    return ('random-own-graveyard-return-v1',({'op':OPERATION,'actor':'$controller','count':count,'selection':selection.to_dict()},),None,(MECHANIC,))
