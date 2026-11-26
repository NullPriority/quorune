from __future__ import annotations

"""Validate rules names before a private search result can move or reveal."""

from ..carddb_characteristics import library_card_rules_names
from ..errors import GameRuleError
from ..library_search_model import DIFFERENT_NAME_SEARCH_FIELD


def validate_search_different_names(host, effect, cards):
    if effect.get(DIFFERENT_NAME_SEARCH_FIELD) is not True:
        return
    seen = set()
    for card in cards:
        record = host.card_record(card)
        if card.zone != "library" or record is None:
            raise GameRuleError("Different-name search requires the original library cards")
        names = {name.casefold().replace("’", "'") for name in library_card_rules_names(record)}
        if not names or not seen.isdisjoint(names):
            raise GameRuleError("Choose creature cards with different names")
        seen.update(names)
