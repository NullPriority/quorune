from __future__ import annotations

"""Ordered existing leaf owners around one query-scaled counter instruction."""

import re
from .closed_effect_programs import ClosedEffectProgramTemplate, _candidate_partitions
from .public_query_effect_amounts import public_query_effect_amount_template


def query_counter_sequence_template(text: str, *, source_name: str, compile_atomic, compile_fixed):
    normalized = text.strip()
    if re.search(r'\b(?:if|unless|may|random|repeat|this way|that much|equal to|different target)\b', normalized, re.I):
        return None
    for clauses in _candidate_partitions(normalized):
        components = []
        scaled = 0
        for clause in clauses:
            material = re.sub(r'^Then\s+', '', clause, flags=re.I)
            compiled = compile_atomic(material)
            if compiled[0] is None:
                compiled = public_query_effect_amount_template(material, source_name=source_name, compile_fixed=compile_fixed)
            if compiled is None or compiled[0] is None or not compiled[1] or not compiled[3]:
                break
            if 'public-query-effect-amount' in compiled[3]:
                if len(compiled[1]) != 1 or compiled[1][0].get('op') != 'place_counters':
                    break
                scaled += 1
            components.append(compiled)
        if len(components) != len(clauses) or scaled != 1:
            continue
        schemas = [component[2] for component in components if component[2] is not None]
        if len(schemas) > 1 or (schemas and schemas[0].get('count') != 1):
            continue
        mechanics = tuple(dict.fromkeys(('closed-effect-program', *(mechanic for component in components for mechanic in component[3]))))
        return ClosedEffectProgramTemplate(
            component_template_ids=tuple(component[0] for component in components),
            component_effect_counts=tuple(len(component[1]) for component in components),
            _effects=tuple(effect for component in components for effect in component[1]),
            _target_schema=schemas[0] if schemas else None, mechanic_ids=mechanics,
        ).compiled()
    return None
