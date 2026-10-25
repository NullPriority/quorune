from __future__ import annotations

"""Compile closed printed variants; runtime consumes keyword values only."""

from ..qualified_hexproof import HEXPROOF_COLORS,QUALIFIED_HEXPROOF_KEYWORDS


def qualified_hexproof_keywords(text):
    value=' '.join(text.casefold().strip().rstrip('.').split())
    if not value.startswith('hexproof from '):return None
    quality=value.removeprefix('hexproof from ')
    if quality=='each color':return tuple('hexproof from '+color for color in HEXPROOF_COLORS)
    values=tuple('hexproof from '+part for part in quality.split(' and from '))
    return values if all(v in QUALIFIED_HEXPROOF_KEYWORDS for v in values) else None
