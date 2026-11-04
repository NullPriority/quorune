from __future__ import annotations

"""Source-threaded fixed animation and independent recipient prevention."""

import re

from ..rules.source_references import SourceReferenceSpec
from .fixed_effect_clause_sequences import fixed_effect_clause_sequence_template, _two_sentence_clauses


def source_animation_prevention_template(text, *, card_name, source_context, compile_clause):
    if (source_context.get('source_is_permanent') is not True
            or 'planeswalker' not in source_context.get('source_card_types', ())):
        return None
    clauses = _two_sentence_clauses(text)
    if clauses is None:
        return None
    match = re.fullmatch(r'Prevent all damage that would be dealt to (?P<subject>.+) this turn\.', clauses[1], re.I)
    if match is None or (match['subject'].casefold() not in {'him', 'her', 'it', 'this planeswalker'}
                         and not SourceReferenceSpec(card_name).matches(match['subject'])):
        return None
    first = compile_clause(clauses[0])
    if (first[0] != 'fixed-resolution-characteristics-v2' or first[2] is not None
            or len(first[1]) != 1 or first[1][0].get('card') != '$source.zone_object'):
        return None
    normalized = clauses[0] + ' Prevent all damage that would be dealt to this permanent this turn.'
    def bound_clause(clause):
        compiled = compile_clause(clause)
        if clause == 'Prevent all damage that would be dealt to this permanent this turn.' and compiled[0] is not None:
            template, effects, schema, mechanics = compiled
            if len(effects) != 1 or effects[0] != {'op': 'create_damage_prevention_shield', 'source': '$source',
                    'subject': '$source', 'mode': 'all', 'duration': 'until_end_of_turn'}:
                return None, (), None, ()
            compiled = (template, ({**effects[0], 'subject': '$source.zone_object', 'source_subject_version': 1},), schema, mechanics)
        return compiled
    result = fixed_effect_clause_sequence_template(normalized, compile_clause=bound_clause)
    return result.compiled() if result is not None else None
