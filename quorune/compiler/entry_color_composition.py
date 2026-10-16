from __future__ import annotations

"""Complete fixed color choices, optionally beside a closed tapped entry leaf."""

import re

from ..entry_designations import (
    ENTRY_COLOR_COMPOSITION_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY,
    EXCLUDED_COLOR_ENTRY_HANDLER_ID,
)
from ..rules.source_references import SourceReferenceSpec
from .entry_state_templates import static_entry_state_handler


def _source_pattern(source_name):
    return (rf'(?:{SourceReferenceSpec(source_name).regex_pattern}|'
            r'this (?:artifact|Aura|card|creature|enchantment|Equipment|land|permanent))')


def _single_color_designation(text, *, source_name):
    from .entry_designation_templates import _plain_entry_designation_handler

    match = re.fullmatch(rf'As {_source_pattern(source_name)} enters, choose a color other than '
                        r'(?P<color>white|blue|black|red|green)\.', text, re.IGNORECASE)
    if match is None:
        return _plain_entry_designation_handler(text, source_name=source_name)
    color = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}[match['color'].casefold()]
    return ('intrinsic-excluded-color-entry-v2', {
        'handler_id': EXCLUDED_COLOR_ENTRY_HANDLER_ID, 'schema_version': 2, 'event': 'zone.change',
        'designation': 'color', 'excluded_color': color,
    }, (ENTRY_DESIGNATION_CAPABILITY, ENTRY_COLOR_COMPOSITION_CAPABILITY))


def composed_entry_color_designation_handler(text, *, source_name):
    lower = text.casefold()
    if 'choose a color' not in lower or not ('other than' in lower or '. as it enters, ' in lower):
        return None
    single = _single_color_designation(text, source_name=source_name)
    if single is not None:
        return single if ENTRY_COLOR_COMPOSITION_CAPABILITY in single[2] else None
    match = re.fullmatch(rf'(?P<subject>{_source_pattern(source_name)}) enters tapped\. '
        r'As it enters, (?P<choice>choose a color(?: other than (?:white|blue|black|red|green))?\.)', text, re.IGNORECASE)
    if match is None:
        return None
    state = static_entry_state_handler(match['subject'] + ' enters tapped.', source_name=source_name)
    choice = _single_color_designation('As ' + match['subject'] + ' enters, ' + match['choice'], source_name=source_name)
    if state is None or choice is None:
        return None
    return ('intrinsic-tapped-entry-color-composition-v1', (state[1], choice[1]),
            tuple(sorted({state[2], *choice[2], ENTRY_COLOR_COMPOSITION_CAPABILITY})))
