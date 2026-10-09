from __future__ import annotations

"""Fixed homogeneous selected-object costs normalized to existing predicates."""

import re

from ..creature_subtypes import canonical_creature_subtype
from .fixed_numbers import fixed_number


_COUNTED = re.compile(
    r'As an additional cost to cast this spell, '
    r'(?P<verb>sacrifice|discard|exile|return) '
    r'(?P<count>two|three|four|five|six|seven|eight|nine|ten|[2-9]|10) '
    r'(?P<quality>.+?)\.?$', re.I,
)
_IRREGULAR = {'elves': 'elf', 'dwarves': 'dwarf', 'wolves': 'wolf', 'allies': 'ally'}
_PLURAL_NOUNS = {
    'cards': 'card', 'artifacts': 'artifact', 'creatures': 'creature',
    'enchantments': 'enchantment', 'lands': 'land', 'permanents': 'permanent',
    'forests': 'forest', 'islands': 'island', 'mountains': 'mountain',
    'swamps': 'swamp', 'plains': 'plains', 'treasures': 'treasure',
    'foods': 'food', 'clues': 'clue',
}


def fixed_counted_zone_change_cost_clause(text: str) -> tuple[str, int] | None:
    """Return one singular closed predicate surface and its fixed count."""
    match = _COUNTED.fullmatch(' '.join(text.strip().split()))
    if match is None:
        return None
    quality = match['quality'].rstrip('.').casefold()
    if any(word in quality for word in (' at random', ' named ', ' other ', ' and ', ' or ', ' with ')):
        return None
    tail = ''
    for marker in (' from your graveyard', ' you control to their owner\'s hand', ' you control to their owners\' hands', ' you control'):
        if quality.endswith(marker):
            quality = quality[:-len(marker)]
            tail = marker.replace('their owners\' hands', "its owner's hand").replace("their owner's hand", "its owner's hand")
            break
    words = quality.split()
    if not words:
        return None
    plural = words[-1]
    noun = _PLURAL_NOUNS.get(plural) or _IRREGULAR.get(plural)
    if noun is None and plural.endswith('s') and not plural.endswith('ss'):
        candidate = plural[:-1]
        noun = canonical_creature_subtype(candidate)
    if noun is None:
        return None
    words[-1] = noun
    singular = ' '.join(words) + tail
    article = 'an' if singular[0] in 'aeiou' else 'a'
    return (
        f"As an additional cost to cast this spell, {match['verb']} {article} {singular}.",
        fixed_number(match['count']),
    )
