from __future__ import annotations

"""Closed fixed-count random graveyard leaves; declared X reuses fixed probes."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype
from ..rules.random_graveyard_returns import (
    OPERATION, MECHANIC, EXILE_CAST_MECHANIC, EXILE_CAST_TEMPLATE, RandomGraveyardReturnSpec, random_exile_cast_instruction,
)


_EXILE_CAST = re.compile(
    r"Exile an instant or sorcery card with mana value (?P<maximum>0|[1-9][0-9]?) or less "
    r"from your graveyard at random\. You may cast it without paying its mana cost\. "
    r"If that spell would be put into a graveyard, exile it instead\.", re.I,
)


def random_graveyard_exile_cast_template(text):
    match = _EXILE_CAST.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    spec = RandomGraveyardReturnSpec("instant or sorcery card", schema_version=2,
                                   maximum_mana_value=int(match["maximum"]))
    return EXILE_CAST_TEMPLATE, (random_exile_cast_instruction("$controller", spec),), None, (EXILE_CAST_MECHANIC,)


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
