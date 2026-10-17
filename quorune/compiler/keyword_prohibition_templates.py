from __future__ import annotations

import re
from typing import Any, Mapping
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES


def opponent_keyword_prohibition_handler(text: str) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    match = re.fullmatch(r"Creatures your opponents control lose (?P<keyword>[a-z ]+) and can't have or gain (?P=keyword)\.?",
        text.strip(), re.IGNORECASE)
    if match is None:
        return None
    keyword = match['keyword'].casefold()
    capabilities = FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES.get(keyword.title())
    if capabilities is None:
        return None
    return ('opponent-keyword-prohibition-v1',
        {'handler_id': 'continuous.ability.opponent-keyword-prohibition.v1', 'schema_version': 1,
         'event': 'characteristics.evaluate', 'keyword': keyword},
        ('continuous.ability.opponent_keyword_prohibition', *capabilities))
