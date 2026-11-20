"""Compose fully owned numeric keyword clauses without merging their instances."""
from . import regex_working_set as re
from .ir_model import SourceSpan
from .keyword_event_effect_nodes import fixed_keyword_event_effect_spec, fixed_keyword_event_effect_node


_NUMERIC = frozenset({'afflict', 'annihilator', 'firebending', 'mobilize', 'soulshift'})


def numeric_keyword_clause_parts(material):
    if ',' not in material:
        return None
    parts = tuple(re.finditer(r'[^,]*', material))
    clauses = []
    for match in parts:
        if match.start() == match.end():
            continue
        raw = match.group();text = raw.strip()
        if not text:
            return None
        spec = fixed_keyword_event_effect_spec(text)
        if spec is None or spec.keyword not in _NUMERIC:
            return None
        start = match.start() + len(raw) - len(raw.lstrip())
        end = match.end() - len(raw) + len(raw.rstrip())
        clauses.append((text, start, end, spec))
    if len(clauses) < 2 or material.strip().startswith(',') or material.strip().endswith(',') or ',,' in material:
        return None
    return tuple(clauses)


def repeated_numeric_keyword_nodes(**values):
    clauses = numeric_keyword_clause_parts(values['material_line'])
    if clauses is None or tuple(values['mechanics']) != tuple(spec.keyword for _, _, _, spec in clauses):
        return None
    nodes = []
    for index, (text, start, end, spec) in enumerate(clauses):
        node = fixed_keyword_event_effect_node(node_id=values['node_id'] + f':numeric-instance:{index + 1}',
            line=text, material_line=text, span=SourceSpan(start=values['span'].start + start,
                end=values['span'].start + end, line=values['span'].line), mechanics=(spec.keyword,),
            capability_registry=values['capability_registry'], capability_profile=values['capability_profile'],
            residuals=values['residuals'])
        if node is None:
            return ()
        nodes.append(node)
    return tuple(nodes)
