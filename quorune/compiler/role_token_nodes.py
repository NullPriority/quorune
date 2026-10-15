from __future__ import annotations

"""Source-pinned predefined Aura Role definitions and closed attached entry."""

from dataclasses import replace
import re
from typing import Any

from ..ability_fragments import StaticComponentSpec, ability_fragment_to_dict
from ..carddb import CardRecord
from ..enchant_spec import SimpleEnchantSpec
from .direct_target import direct_permanent_target_spec
from .generated_program_identity import generated_ability_id
from .ir_model import OracleNode

ROLE_TOKEN_CAPABILITY = 'token.creation.predefined_role'
ROLE_TOKEN_MECHANIC = 'predefined-role-token'
ROLE_DEFINITIONS = {
    'Cursed': ('Enchanted creature has base power and toughness 1/1.',),
    'Monster': ('Enchanted creature gets +1/+1 and has trample.',),
    'Royal': ('Enchanted creature gets +1/+1 and has ward {1}.',),
    'Sorcerer': ('Enchanted creature gets +1/+1 and has "Whenever this creature attacks, scry 1."',),
    'Virtuous': ('Enchanted creature gets +1/+1 for each enchantment you control.',),
    'Wicked': ('Enchanted creature gets +1/+1.',
        'When this enchantment is put into a graveyard from the battlefield, each opponent loses 1 life.'),
    'Young Hero': ('Enchanted creature has "Whenever this creature attacks, if its toughness is 3 or less, put a +1/+1 counter on it."',),
}
_CREATION = re.compile(
    r'Create a (?P<role>Cursed|Monster|Royal|Sorcerer|Virtuous|Wicked|Young Hero) Role token '
    r'attached to (?P<subject>(?:up to one )?(?:another )?target creature(?: you control| an opponent controls)?|it)\.', re.I)


def role_creation_parts(text: str):
    match = _CREATION.fullmatch(text.strip())
    if match is None:
        return None
    role = next(name for name in ROLE_DEFINITIONS if name.casefold() == match['role'].casefold())
    subject = match['subject']
    if subject.casefold() == 'it':
        return role, '$source.zone_object', None
    optional = subject.casefold().startswith('up to one ')
    direct = direct_permanent_target_spec(subject.removeprefix('up to one '))
    if direct is None:
        return None
    schema = direct.to_target_schema()
    if optional:
        schema.pop('count', None)
        schema.update(min=0, max=1)
    return role, '$target.0', schema


def role_definition_children(*, role: str, record: Any, face_id: str, node_id: str, line: str,
        span: Any, capability_registry: Any, capability_profile: str):
    from ..oracle_ir import compile_oracle_card
    from ..semantic_runtime.ability_fragments import fragments_from_descriptors
    from .attached_granted_ability_nodes import attached_granted_ability_plan
    text = '\n'.join(ROLE_DEFINITIONS[role])
    token = CardRecord(oracle_id=record.oracle_id, name='Created Role', mana_cost='', mana_value=0,
        type_line='Token Enchantment — Aura Role', oracle_text=text, power=None, toughness=None,
        loyalty=None, defense=None, colors=(), color_identity=(), keywords=(), produced_mana=(),
        layout='normal', released_at=record.released_at, legalities=record.legalities, faces=(), raw={})
    ir = compile_oracle_card(token, capability_registry=capability_registry, capability_profile=capability_profile)
    if ir.status != 'exact':
        return None
    fragments = [ability_fragment_to_dict(SimpleEnchantSpec('creature'))]
    children = []
    for index, child in enumerate(n for f in ir.faces for n in f.nodes):
        child_id = f'{node_id}:role-static:{index + 1}'
        if child.kind == 'granted_triggered_ability':
            # The parent grant fragment is rewritten after all child IDs are known.
            child_id = f'{node_id}:token-ability:{index + 1}'
            child = replace(child, node_id=child_id, span=span)
            children.append(child)
            continue
        if child.kind == 'triggered_ability':
            child_id = f'{node_id}:token-ability:{index + 1}'
            plan = attached_granted_ability_plan(node=child, quoted_text=child.text,
                oracle_id=record.oracle_id, face_id=face_id, source_line=span.line,
                card_name='Created Role', keywords=(), node_id=child_id)
            if plan is None:
                return None
            fragments.append(plan.fragment)
            children.append(replace(child, node_id=child_id, kind=plan.node_kind, text=line, span=span))
            continue
        if not child.handlers:
            return None
        child = replace(child, node_id=child_id, kind='granted_static_ability', text=line, span=span)
        ability_id = generated_ability_id(kind=child.kind, face_id=face_id, line=span.line,
            static_declaration=True, node_id=child_id)
        fragments.append(ability_fragment_to_dict(StaticComponentSpec(f'{record.oracle_id}:{ability_id}')))
        children.append(child)
    aliases = {}
    for old, child in zip((n for f in ir.faces for n in f.nodes), children, strict=True):
        old_id = generated_ability_id(kind=old.kind, face_id=ir.faces[0].face_id, line=old.span.line,
            static_declaration=bool(old.handlers), node_id=old.node_id)
        new_id = generated_ability_id(kind=child.kind, face_id=face_id, line=span.line,
            static_declaration=bool(child.handlers), node_id=child.node_id)
        aliases[f'{record.oracle_id}:{old_id}'] = f'{record.oracle_id}:{new_id}'
        aliases[old_id] = new_id
    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [rewrite(child) for child in value]
        if isinstance(value, str) and value in aliases:
            return aliases[value]
        return value
    result = []
    for child in children:
        handlers = [rewrite(dict(d)) for d in child.handlers]
        for descriptor in handlers:
            modifier = descriptor.get('modifier', {})
            grants = modifier.get('add_ability_fragments', [])
            for grant in tuple(grants):
                if grant.get('kind') == 'granted_triggered':
                    grants.append(ability_fragment_to_dict(StaticComponentSpec(grant['value']['semantic_key'])))
        result.append(replace(child, text=line, handlers=tuple(handlers)))
    return fragments, tuple(result)


def role_token_nodes(*, record, face_id, node_id, line, material_line, span, source_name,
        effect_template, keywords, printed_card_types, source_attachment_relation, trusted_mechanics,
        capability_registry, capability_profile, compile_inner, trigger_node, grant_effect_templates):
    if ' Role token' not in material_line or capability_registry is None:
        return None
    match = re.search(r'Create a (?:Cursed|Monster|Royal|Sorcerer|Virtuous|Wicked|Young Hero) Role token attached to [^.]+\.', material_line, re.I)
    if match is None:
        return None
    parsed = role_creation_parts(match[0])
    if parsed is None:
        return None
    role, target_ref, target_schema = parsed
    compiled = role_definition_children(role=role, record=record, face_id=face_id, node_id=node_id,
        line=line, span=span, capability_registry=capability_registry, capability_profile=capability_profile)
    if compiled is None:
        return None
    fragments, children = compiled
    dependencies = {ROLE_TOKEN_CAPABILITY, *(cap for child in children for cap in child.capability_dependencies)}
    closure = capability_registry.closure(dependencies, profile=capability_profile)
    if not closure.trusted:
        return None
    effect = {'op': 'create_role_token', 'controller': '$controller', 'name': role, 'quantity': 1,
        'aura_target_ref': target_ref, 'characteristics': {'type_line': 'Token Enchantment — Aura Role',
            'colors': [], 'ability_fragments': fragments}}
    def role_effect(text, **kwargs):
        from ..oracle_ir import _without_parenthetical_reminder
        material = _without_parenthetical_reminder(text).strip()
        if material.rstrip('.').casefold() == match[0].rstrip('.').casefold():
            return ('create-predefined-role-v1', (effect,), target_schema,
                (ROLE_TOKEN_MECHANIC, 'cr-111-tokens', 'cr-115-targets') if target_schema else (ROLE_TOKEN_MECHANIC, 'cr-111-tokens'))
        return effect_template(material, **kwargs)
    def role_grants(*args):
        ordinary, triggered = grant_effect_templates(*args)
        def wrap(fallback):
            return lambda text, **kwargs: role_effect(text, **kwargs) if 'Role token' in text else fallback(text, **kwargs)
        return wrap(ordinary), wrap(triggered)
    from .typed_token_ability_nodes import _outer_nodes
    def role_trigger(**kwargs):
        from functools import partial
        underlying = trigger_node.func if isinstance(trigger_node, partial) else trigger_node
        bound = dict(trigger_node.keywords or {}) if isinstance(trigger_node, partial) else {}
        bound.update(kwargs)
        bound['effect_template'] = role_grants(True, printed_card_types, source_attachment_relation)[1]
        return underlying(**bound)
    outer = _outer_nodes(node_id=node_id, line=line, shell=material_line, span=span,
        source_name=source_name, printed_card_types=printed_card_types, keywords=keywords,
        source_attachment_relation=source_attachment_relation, effect_template=role_effect,
        compile_inner=compile_inner, trigger_node=role_trigger, grant_effect_templates=role_grants,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry, capability_profile=capability_profile)
    if outer is None:
        return None
    result = []
    for node in outer:
        deps = tuple(sorted(dependencies | set(node.capability_dependencies)))
        bound = capability_registry.closure(deps, profile=capability_profile)
        if not bound.trusted:
            return None
        result.append(replace(node, capability_dependencies=deps, capability_closure=bound.reachable,
            capability_profile=bound.profile, capability_fingerprint=bound.fingerprint))
    return (*result, *children)
