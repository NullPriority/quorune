from __future__ import annotations

"""Source-spanned creation of independently closed command-zone abilities."""

from dataclasses import replace
import re

from ..emblem_program_model import CompiledEmblemSpec, EmblemAbilityProgram, EMBLEM_CHILD_COVERAGE, EMBLEM_PROGRAM_CAPABILITY
from .dependency_gate import explicit_capabilities_gate, dependency_gate
from .generated_program_identity import generated_ability_id
from .runtime_templates import static_runtime_template
from .static_runtime_nodes import runtime_handler_node
from .fixed_counter_trigger_nodes import fixed_typed_event_effect_trigger_node


def _emblem_child(body, *, node_id, span, registry, profile, effects, trusted_mechanics):
    residuals = []
    template = static_runtime_template(body, source_name='Emblem', source_damageable=False, source_permanent=False)
    if template is not None and template.kind == 'static_ability' and template.event == 'characteristics.evaluate':
        child = runtime_handler_node(node_id=node_id, line=body, span=span, compiled=template.compiled,
            kind='granted_static_ability', event=template.event, active_zone='command',
            runtime_coverage=(*template.runtime_coverage, EMBLEM_CHILD_COVERAGE), mechanics=template.mechanics,
            dependency_reason=template.dependency_reason, capability_registry=registry,
            capability_profile=profile, residuals=residuals)
    else:
        child = fixed_typed_event_effect_trigger_node(node_id=node_id, line=body, material_line=body,
            span=span, card_name='Emblem', effect_template=effects, trusted_mechanics=trusted_mechanics,
            capability_registry=registry, capability_profile=profile, residuals=residuals)
        if child is not None:
            child = replace(child, kind='granted_triggered_ability', active_zone='command',
                runtime_coverage=(*child.runtime_coverage, EMBLEM_CHILD_COVERAGE))
    if child is None or not child.exact or residuals:
        return None
    def battlefield_reference(value):
        if isinstance(value, dict):
            return any(battlefield_reference(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(battlefield_reference(v) for v in value)
        return value == '$source.zone_object'
    if battlefield_reference(child.effects):
        return None
    gate = explicit_capabilities_gate((*child.capability_dependencies, EMBLEM_PROGRAM_CAPABILITY),
        capability_registry=registry, capability_profile=profile)
    if gate.blockers or gate.closure is None:
        return None
    return replace(child, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable, capability_profile=gate.closure.profile,
        capability_fingerprint=gate.closure.fingerprint)


def compiled_emblem_nodes(*, record, face_id, node_id, line, material_line, span, source_name,
                         effect_template, keywords, printed_card_types, source_attachment_relation,
                         trusted_mechanics, capability_registry, capability_profile,
                         compile_inner, trigger_node, grant_effect_templates):
    match = re.search(r'You get an emblem with (?P<quote>"[^\"]+"(?: and "[^\"]+")?)\.?$', material_line)
    if match is None or capability_registry is None:
        return None
    instruction = match.group(0)
    bodies = re.findall(r'"([^\"]+)"', match['quote'])
    source_effects, _ = grant_effect_templates(False, (), None)
    def emblem_effect(text, **kwargs):
        from .damage_templates import source_pronoun_damage_effect_template
        damage = re.fullmatch(r'This emblem deals (?P<body>.+)', text, re.I)
        if damage is not None:
            leaf = source_pronoun_damage_effect_template('It deals ' + damage['body'])
            if leaf is not None:
                return leaf.compiled()
        return source_effects(text, **kwargs)
    children = tuple(_emblem_child(body, node_id=f'{node_id}:emblem-ability:{slot}', span=span,
        registry=capability_registry, profile=capability_profile, effects=emblem_effect,
        trusted_mechanics=trusted_mechanics) for slot, body in enumerate(bodies, 1))
    if any(child is None for child in children):
        return None
    references = tuple(EmblemAbilityProgram(
        semantic_key=record.oracle_id + ':' + generated_ability_id(kind=child.kind, face_id=face_id,
            line=span.line, static_declaration=bool(child.handlers), node_id=child.node_id),
        kind='static' if child.handlers else 'triggered', event=child.event, text=child.text,
        capabilities=tuple(sorted(child.capability_dependencies))) for child in children)
    spec = CompiledEmblemSpec(record.oracle_id, references)
    compiled = ('create-compiled-emblem-v1', ({'op': 'create_compiled_emblem', 'controller': '$controller',
        'emblem': spec.to_dict()},), None, ())
    def outer_effect(original):
        def compile_body(text, **kwargs):
            normalized = text.strip().rstrip('.')
            return compiled if normalized in {instruction.rstrip('.'), 'You get an emblem with'} else original(text, **kwargs)
        return compile_body
    def outer_templates(*args):
        first, second = grant_effect_templates(*args)
        return outer_effect(first), outer_effect(second)
    from .typed_token_ability_nodes import _outer_nodes
    parents = _outer_nodes(node_id=node_id, line=line, shell=material_line, span=span,
        source_name=source_name, printed_card_types=printed_card_types, keywords=keywords,
        source_attachment_relation=source_attachment_relation, effect_template=outer_effect(effect_template),
        compile_inner=compile_inner, trigger_node=trigger_node, grant_effect_templates=outer_templates,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile)
    if parents is None:
        return None
    closed = []
    for parent in parents:
        gate = dependency_gate(mechanics=parent.mechanics, effects=parent.effects,
            target_schema=parent.target_schema, cost_schema=parent.cost,
            trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
            capability_profile=capability_profile,
            explicit_capabilities=(*parent.capability_dependencies, *spec.capabilities))
        if gate.blockers or gate.closure is None:
            return None
        closed.append(replace(parent, capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable, capability_profile=gate.closure.profile,
            capability_fingerprint=gate.closure.fingerprint))
    return (*closed, *children)
