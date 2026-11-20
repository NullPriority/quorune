from __future__ import annotations

"""One fixed, optional hand spell cast, with the printed mana-value ceiling."""

from . import regex_working_set as re
from ..bounded_hand_cast_model import BoundedHandCastSpec
from ..mana_value_limit import ManaValueLimitSpec


def bounded_hand_cast_template(text):
    match = re.fullmatch(r'You may cast a spell with mana value (?P<maximum>0|[1-9][0-9]*) or less '
        r'from your hand without paying its mana cost\.', ' '.join(text.strip().split()), re.I)
    if match is None:
        return None
    return BoundedHandCastSpec(ManaValueLimitSpec(int(match['maximum'])))
