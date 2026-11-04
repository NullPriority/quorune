from __future__ import annotations

"""Resolve only the inspection count; selection semantics keep their owner."""

import re

from ..variable_library_selection_model import (ALL_LOOKED,VARIABLE_LIBRARY_SELECTION_OPERATION,
    VARIABLE_LIBRARY_SELECTION_MECHANIC,validate_variable_library_selection)
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template
from .fixed_library_selection_templates import fixed_library_selection_effect_template
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .oracle_source_text import without_ability_word_label


_HEADER=re.compile(r'(?P<header>(?:Look at|Reveal) the top X cards of your library)'
    r'(?P<definition>, where X is (?:equal to )?[^.]+)?\.\s*(?P<tail>.+)',re.I)


def variable_library_selection_effect_template(text,*,source_name,cast_x_available=False,
        forbid_public_x=False,**context):
    material=without_ability_word_label(' '.join(text.strip().split()))
    match=_HEADER.fullmatch(material) if material is not None else None
    if match is None:return None
    # A probe larger than every printed numeric literal cannot collide with a
    # printed selection maximum. It is never emitted or used for inspection.
    probe=max([2,*(fixed_number(number[0]) for number in re.finditer(
        rf'\b(?:{FIXED_COUNT_PATTERN})\b',match['tail'],re.I))])+1
    fixed=fixed_library_selection_effect_template(re.sub(r'\bX\b',str(probe),match['header'])+'. '+match['tail'])
    if fixed is None or fixed.selected_destination!='hand':return None
    selection=fixed.compiled()[1][0]
    maximum=ALL_LOOKED if fixed.maximum==probe else fixed.maximum
    def header(body):
        numeric=re.fullmatch(r'(?:Look at|Reveal) the top (?P<count>[0-9]+) cards of your library\.?',body,re.I)
        if numeric is None:return None,(),None,()
        effect={**selection,'op':VARIABLE_LIBRARY_SELECTION_OPERATION,'schema_version':1,
            'look_count':int(numeric['count']),'maximum':maximum}
        try:validate_variable_library_selection(effect)
        except (TypeError,ValueError,KeyError):return None,(),None,()
        return 'variable-library-selection-v1',(effect,),None,(VARIABLE_LIBRARY_SELECTION_MECHANIC,)
    amount_source=match['header']+(match['definition'] or '')+'.'
    return declared_effect_amount_template(amount_source,source_name=source_name,compile_fixed=header,
        cast_x_available=cast_x_available,forbid_public_x=forbid_public_x) or scalar_effect_amount_template(
            amount_source,source_name=source_name,compile_fixed=header,
            event=context.get('event'),source_event=context.get('source_event',False))
