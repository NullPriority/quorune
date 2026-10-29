"""Closed Oracle keyword grammar; these values do not grant runtime trust."""
from __future__ import annotations

import re

from ..champion_model import ChampionSpec
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec


def champion_spec(text):
    match = re.fullmatch(r'Champion (?:a|an) (?P<subject>[A-Za-z]+(?: or [A-Za-z]+)?)', text.strip(), re.I)
    if match is None:
        return None
    subject = match['subject'].casefold()
    if subject == 'creature':
        return ChampionSpec(ObjectQuerySpec(zones=('battlefield',), types_all=('creature',)))
    values = tuple(canonical_creature_subtype(word) for word in subject.split(' or '))
    if any(value is None for value in values) or len(set(values)) != len(values):
        return None
    return ChampionSpec(ObjectQuerySpec(zones=('battlefield',), subtypes_any=values))
