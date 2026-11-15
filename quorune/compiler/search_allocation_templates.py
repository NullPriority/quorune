"""Whole fixed searches whose ordered found cards have distinct destinations."""
import re

from ..library_search_model import FixedCountedLibrarySearchTemplate, search_selector
from ..search_allocation_model import SearchAllocationSpec
from ..replacement.immutable import FrozenMap
from .library_search_templates import _FIXED_SEARCH, _search_query, _disjunctive_selector
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .oracle_source_text import without_parenthetical_reminder
from .closed_effect_programs import ClosedEffectProgramTemplate, CLOSED_EFFECT_PROGRAM_MECHANIC


def _selector(quality):
    if not quality:return {}
    alternative = _disjunctive_selector(quality, destination='hand', mana_value=None)
    if alternative is not None:return alternative
    query = _search_query(quality, destination='hand')
    return search_selector(query) if query is not None else None


def search_allocation_template(text):
    if not text.lstrip().casefold().startswith('search your library for '):return None
    text = ' '.join(without_parenthetical_reminder(text).strip().split())
    multi = _FIXED_SEARCH.fullmatch(text)
    if multi is not None and not multi['hand'] and not multi['tapped']:
        count = fixed_number(multi['count']);selector = _selector(multi['quality'])
        if count > 1 and selector is not None:
            try:
                base = FixedCountedLibrarySearchTemplate(count, bool(multi['up_to']), FrozenMap(selector), 'hand', bool(multi['reveal']))
                return SearchAllocationSpec(base, count, 'battlefield', 'hand').compiled()
            except ValueError:return None
    head = re.fullmatch(rf'Search your library for (?P<optional>up to )?(?P<count>{FIXED_COUNT_PATTERN}) '
        r'(?:(?P<quality>.+?) )?cards(?:, reveal (?P<comma_reveal>those cards|them)| and reveal (?P<and_reveal>those cards|them))?'
        r'(?:\. |, (?:and )?)(?P<tail>.+)', text, re.I)
    if head is None:return None
    tail = re.fullmatch(r'Put (?P<count>one|two)(?: of them)? (?P<first>onto the battlefield(?P<tapped> tapped)?|into your hand) '
        r'and (?:the other|the rest) (?P<rest>into your hand|into your graveyard)'
        r'(?:, then shuffle\.|\. Then shuffle\.|\. Shuffle, then scry (?P<scry>1)\.)', head['tail'], re.I)
    if tail is None:return None
    selector = _selector(head['quality'])
    if selector is None:return None
    try:
        base = FixedCountedLibrarySearchTemplate(fixed_number(head['count']), bool(head['optional']), FrozenMap(selector), 'hand', bool(head['comma_reveal'] or head['and_reveal']))
        spec = SearchAllocationSpec(base, fixed_number(tail['count']), 'battlefield' if tail['first'].casefold().startswith('onto') else 'hand',
            'hand' if tail['rest'].casefold() == 'into your hand' else 'graveyard', bool(tail['tapped']))
    except ValueError:return None
    compiled = spec.compiled()
    if tail['scry'] is None:return compiled
    scry = {'op': 'scry', 'player': '$controller', 'count': 1}
    mechanics = tuple(dict.fromkeys((CLOSED_EFFECT_PROGRAM_MECHANIC, *compiled[3], 'scry')))
    return ClosedEffectProgramTemplate((compiled[0], 'scry-controller-v1'), (1, 1), (*compiled[1], scry), None, mechanics).compiled()
