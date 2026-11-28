from __future__ import annotations

"""Compile the exact optional two-name search and opponent's public choice."""

from ..rules.searched_library_pairs import SEARCHED_PAIR_MECHANICS, searched_pair_optional_effect
from . import regex_working_set as re


_PAIR_ENTRY = re.compile(
    r"You may search your library for exactly two cards not named (?P<excluded>.+?) that have different names\. "
    r"If you do, reveal those cards\. An opponent chooses one of them\. "
    r"Put the chosen card into your hand and the other into your graveyard, then shuffle\.",
    re.IGNORECASE,
)


def searched_pair_entry_template(body, *, card_name):
    match = _PAIR_ENTRY.fullmatch(" ".join(body.split()))
    if match is None or match["excluded"].casefold() != card_name.casefold():
        return None
    effect = searched_pair_optional_effect(card_name)
    return "optional-searched-pair-opponent-choice-v1", (effect,), None, SEARCHED_PAIR_MECHANICS
