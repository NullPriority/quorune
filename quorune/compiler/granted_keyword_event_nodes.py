"""Grant existing exact keyword event programs through typed ability shells."""
from __future__ import annotations

from dataclasses import replace
import re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .conditional_granted_ability_templates import (
    conditional_quoted_ability_handler, conditional_quoted_ability_text,
)
from .continuous_templates import (
    attached_quoted_ability_handler, attached_quoted_ability_text,
    fixed_query_quoted_ability_handler, fixed_query_quoted_ability_text,
)
from .keyword_event_effect_nodes import fixed_keyword_event_effect_node, fixed_keyword_event_effect_spec


GRANTED_KEYWORD_EVENT_CAPABILITY = 'continuous.ability.fixed_keyword_event_grant'
_KEYWORD = re.compile(r'\b(?:Afflict|Annihilator|Firebending|Mobilize|Soulshift) [1-9]\d*\b'
                      r'|\b(?:Ingest|Exploit|Provoke)\b', re.I)


def _grant_shell(material_line, source_name):
    if '"' in material_line:
        return None
    matches = tuple(_KEYWORD.finditer(material_line))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.search(r'\b(?:has|have)\b', material_line[:match.start()], re.I) is None:
        return None
    keyword = match.group()
    spec = fixed_keyword_event_effect_spec(keyword)
    if spec is None:
        return None
    synthetic = material_line[:match.start()] + '"' + keyword + '"' + material_line[match.end():]
    if attached_quoted_ability_text(synthetic, source_name=source_name) == keyword:
        compile_shell = lambda fragment, caps: attached_quoted_ability_handler(synthetic,
            source_name=source_name, fragment=fragment, fragment_capabilities=caps)
    elif fixed_query_quoted_ability_text(synthetic) == keyword:
        compile_shell = lambda fragment, caps: fixed_query_quoted_ability_handler(synthetic,
            fragment=fragment, fragment_capabilities=caps)
    elif conditional_quoted_ability_text(synthetic, source_name=source_name) == keyword:
        compile_shell = lambda fragment, caps: conditional_quoted_ability_handler(synthetic,
            source_name=source_name, fragment=fragment, fragment_capabilities=caps)
    else:
        return None
    return keyword, spec, compile_shell


def _keyword_inner_node(spec, **values):
    node = fixed_keyword_event_effect_node(node_id=values['node_id'], line=values['line'],
        material_line=values['material_line'], span=values['span'], mechanics=(spec.keyword,),
        capability_registry=values['capability_registry'], capability_profile=values['capability_profile'],
        residuals=values['residuals'])
    if node is None:
        return None
    # A granted-only program is discovered through its current grant fragment.
    # Its receiver does not own the printed source's static-component marker.
    return replace(node, runtime_coverage=tuple(value for value in node.runtime_coverage
                                             if value != CURRENT_ABILITY_FRAGMENT_COVERAGE))


def compile_granted_keyword_event_nodes(*, record, face_id, node_id, line, material_line, span,
        source_name, trusted_mechanics, capability_registry, capability_profile, residuals,
        effect_template, trigger_effect_template, material_line_for):
    shell = _grant_shell(material_line, source_name)
    if shell is None:
        return None
    keyword, spec, compile_shell = shell

    def bind_shell(fragment, fragment_capabilities):
        compiled = compile_shell(fragment, fragment_capabilities)
        if compiled is None:
            return None
        _template_id, descriptor, capabilities = compiled
        return ('continuous-fixed-keyword-event-grant-v1', descriptor,
                tuple(sorted({*capabilities, GRANTED_KEYWORD_EVENT_CAPABILITY})))

    from .attached_granted_ability_nodes import _compile_granted_ability_pair
    return _compile_granted_ability_pair(record=record, face_id=face_id, node_id=node_id,
        line=line, material_line=material_line, span=span, quoted=keyword, compile_shell=bind_shell,
        runtime_coverage='fixed_keyword_event_grant',
        dependency_reason='A fixed keyword grant requires its ordinary inner program and current grant owner',
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
        compile_inner=lambda **values: _keyword_inner_node(spec, **values),
        effect_template=effect_template, trigger_effect_template=trigger_effect_template,
        material_line_for=material_line_for)
