from __future__ import annotations

import re
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .untap_step_templates import _descriptor


def fixed_untap_selection_limit_handler(text: str, *, source_name: str):
    match = re.fullmatch(rf"(?:(?:As long as (?P<source>.+?) is untapped, )?players|(?P<self>You)) "
        rf"can't untap more than (?P<count>{FIXED_COUNT_PATTERN}) (?P<kind>permanent|artifact|creature|nonbasic land|land)s? "
        r"during (?:their untap steps|your untap step)\.?",text.strip(),re.I)
    if match is None:
        return None
    if bool(match['self']) != ('during your untap step' in text.casefold()):
        return None
    if match['source'] and source_self_permanent_type(match['source']) is None and not SourceReferenceSpec(source_name).matches(match['source']):
        return None
    maximum = fixed_number(match['count'])
    if not 0 <= maximum <= 10:
        return None
    kind = match['kind'].casefold()
    predicate = ObjectQuerySpec(zones=('battlefield',), types_all=() if kind=='permanent' else
        ('land',) if kind=='nonbasic land' else (kind,), excluded_supertypes=('basic',) if kind=='nonbasic land' else ())
    descriptor = _descriptor(instruction='limit',subject_relation='query',
        controller_relation='source_controller' if match['self'] else 'any',creature=False,
        turn_relation='subject_controller',source_state='untapped' if match['source'] else 'any',
        maximum=maximum,predicate=predicate)
    descriptor['handler_id']='participation.untap-step.fixed-selection-limit.v1'
    return 'fixed-untap-selection-limit-v1',descriptor,'untap.step.fixed_selection_limit'
