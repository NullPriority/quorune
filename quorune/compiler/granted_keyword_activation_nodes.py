from __future__ import annotations

"""Compile fixed Equip and Crew keywords through existing typed grant owners."""

from dataclasses import replace
from . import regex_working_set as re

from .conditional_granted_ability_templates import conditional_quoted_ability_handler, conditional_quoted_ability_text
from .continuous_templates import fixed_query_quoted_ability_handler, fixed_query_quoted_ability_text


_KEYWORD = re.compile(r'\b(?:crew (?:0|[1-9]\d*)\b|equip (?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)', re.I)


def equip_keyword_catalog_node(node, *, cost_text, registry, profile):
    """Retain the keyword's typed identity beside its existing attachment result."""
    from ..abilities import parse_activated_abilities
    from ..replacement.immutable import thaw_value
    from ..semantic_runtime.activated_abilities import activated_ability_catalog_descriptor
    abilities = parse_activated_abilities(card_name='Granted Equipment',
        oracle_text='Equip ' + cost_text, keywords=('Equip',))
    if len(abilities) != 1:
        return None
    ability = abilities[0]
    if (ability.builtin_semantic_key != 'builtin:equip' or not ability.compiled_cost
            or thaw_value(node.target_schema) != thaw_value(ability.target_schema)):
        return None
    dependencies = tuple(sorted({*node.capability_dependencies, 'attachment.equip.fixed_mana'}))
    closure = registry.closure(dependencies, profile=profile)
    if not closure.trusted:
        return None
    return replace(node, handlers=(*node.handlers,
        activated_ability_catalog_descriptor(ability, requires_complete_card_program=True)),
        capability_dependencies=dependencies, capability_closure=closure.reachable,
        capability_profile=closure.profile, capability_fingerprint=closure.fingerprint)


def fixed_keyword_activation_shell(text, *, source_name):
    """Separate one complete fixed activation keyword from a closed grant shell."""
    if '"' in text:
        return None
    matches = tuple(_KEYWORD.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.search(r'\b(?:has|have)\b', text[:match.start()], re.I) is None:
        return None
    keyword = match.group()
    crew = keyword.casefold().startswith('crew ')
    if crew:
        inner_text = 'Crew ' + keyword.split()[-1]
    else:
        from ..rules.attachment_actions import fixed_equip_ability_spec
        spec = fixed_equip_ability_spec('Equip ' + keyword.partition(' ')[2])
        if spec is None or spec.requires_catalog or spec.restricted or spec.once_per_turn:
            return None
        inner_text = spec.cost_text + ': Attach this Equipment to target creature you control. Activate only as a sorcery.'
    synthetic = text[:match.start()] + '"' + inner_text + '"' + text[match.end():]
    if fixed_query_quoted_ability_text(synthetic) == inner_text:
        shell = lambda fragment, caps: fixed_query_quoted_ability_handler(synthetic,
            fragment=fragment, fragment_capabilities=caps)
    elif conditional_quoted_ability_text(synthetic, source_name=source_name) == inner_text:
        shell = lambda fragment, caps: conditional_quoted_ability_handler(synthetic,
            source_name=source_name, fragment=fragment, fragment_capabilities=caps)
    else:
        return None
    return crew, inner_text, shell


def compile_granted_keyword_activation_nodes(*, record, face_id, node_id, line, material_line,
        span, source_name, trusted_mechanics, capability_registry, capability_profile,
        residuals, effect_template, trigger_effect_template, material_line_for):
    parsed = fixed_keyword_activation_shell(material_line, source_name=source_name)
    if parsed is None or capability_registry is None:
        return None
    crew, inner_text, compile_shell = parsed

    def compile_inner(**values):
        if crew:
            from .crew_nodes import ordinary_crew_keyword_node
            return ordinary_crew_keyword_node(node_id=values['node_id'], line=inner_text,
                material_line=inner_text, span=values['span'], mechanics=('crew',),
                capability_registry=capability_registry, capability_profile=capability_profile,
                residuals=values['residuals'])
        from ..oracle_ir import compile_oracle_card
        token = replace(record, name='Granted Equipment', type_line='Artifact — Equipment',
            oracle_text=inner_text, mana_cost='', mana_value=0, power=None, toughness=None,
            loyalty=None, defense=None, keywords=(), layout='normal', faces=(), raw={})
        ir = compile_oracle_card(token, capability_registry=capability_registry,
            capability_profile=capability_profile)
        if ir.status != 'exact' or len(ir.faces) != 1 or len(ir.faces[0].nodes) != 1:
            return None
        node = replace(ir.faces[0].nodes[0], node_id=values['node_id'], span=values['span'])
        return equip_keyword_catalog_node(node, cost_text=inner_text.partition(':')[0],
            registry=capability_registry, profile=capability_profile)

    def bind_shell(fragment, capabilities):
        return compile_shell(fragment, tuple(sorted({*capabilities,
            *(('attachment.equip.fixed_mana',) if not crew else ())})))

    from .attached_granted_ability_nodes import _compile_granted_ability_pair
    return _compile_granted_ability_pair(record=record, face_id=face_id, node_id=node_id,
        line=line, material_line=material_line, span=span, quoted=inner_text,
        compile_shell=bind_shell, runtime_coverage='fixed_keyword_activation_grant',
        dependency_reason='Fixed keyword activation grants require their ordinary cost and current grant owners',
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals, compile_inner=compile_inner,
        effect_template=effect_template, trigger_effect_template=trigger_effect_template,
        material_line_for=material_line_for)
