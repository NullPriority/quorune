from __future__ import annotations

"""Fixed Equipment token characteristics and source-pinned intrinsic programs."""

from dataclasses import replace
from . import regex_working_set as re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, StaticComponentSpec, ability_fragment_to_dict
from ..fixed_token_production import (
    FIXED_TOKEN_PRODUCTION_MECHANIC_ID, TOKEN_LITERAL_NAME_VERSION_FIELD,
    FixedTokenCreationTemplate,
)


_NAME = r"[A-Z][A-Za-z' -]*(?:, [A-Z][A-Za-z' -]*)?"
_BODY = r'(?P<color>colorless|white|blue|black|red|green) (?P<book>Book )?Equipment artifact token'
_NAMED = re.compile(r'Create a ' + _BODY + r' named (?P<name>' + _NAME + r')\.?', re.I)
_LEGENDARY = re.compile(r'Create (?P<name>' + _NAME + r'), a legendary ' + _BODY + r'\.?', re.I)
_COLORS = {'white':'W', 'blue':'U', 'black':'B', 'red':'R', 'green':'G'}
_QUOTED = re.compile(r'"([^\"]+)"')
_ABILITY_TAIL = re.compile(r'"[^\"]+"(?:,? (?:and )?"[^\"]+")?,? and equip (?P<cost>\{(?:0|[1-9]\d*)\})\.', re.I)


def fixed_named_equipment_token_template(text):
    match = _NAMED.fullmatch(text.strip()) or _LEGENDARY.fullmatch(text.strip())
    if match is None:
        return None
    legendary = _LEGENDARY.fullmatch(text.strip()) is not None
    color = _COLORS.get(match['color'].lower())
    return FixedTokenCreationTemplate(
        'create-fixed-named-equipment-token-v1',
        {'op':'create_token', 'controller':'$controller', 'name':match['name'].strip(), 'quantity':1,
         'characteristics':{'type_line':'Token ' + ('Legendary ' if legendary else '') +
             'Artifact — ' + ('Book ' if match['book'] else '') + 'Equipment',
             'colors':[color] if color else [], TOKEN_LITERAL_NAME_VERSION_FIELD:1}},
        (FIXED_TOKEN_PRODUCTION_MECHANIC_ID, 'cr-111-tokens', 'fixed-named-token'),
    )


def named_equipment_ability_shell(text):
    """Consume the entire quoted-definition and ordinary fixed Equip suffix."""
    matches = tuple(_QUOTED.finditer(text))
    if not 1 <= len(matches) <= 2:
        return None
    prefix = text[:matches[0].start()]
    if not prefix.endswith(' with ') or 'Equipment artifact token' not in prefix:
        return None
    tail = _ABILITY_TAIL.fullmatch(text[matches[0].start():])
    if tail is None:
        return None
    shell = prefix.removesuffix(' with ') + '.'
    quoted = tuple(m[1].rstrip(',. ') + '.' for m in matches)
    return shell, quoted, tail['cost']


def equipment_token_children(*, record, characteristics, name, quoted, equip_cost,
        face_id, node_id, span, capability_registry, capability_profile):
    """Compile the token as a whole, preserving nested grants and their aliases."""
    from ..oracle_ir import compile_oracle_card
    from .attached_granted_ability_nodes import attached_granted_ability_plan
    from .generated_program_identity import generated_ability_id

    # CR 702.6a is a compiler expansion. Runtime receives only typed costs,
    # targets and attachment effects, just as it does for a printed activation.
    equip_text = equip_cost + ': Attach this Equipment to target creature you control. Activate only as a sorcery.'
    abilities = []
    for ability in quoted:
        if " has '" in ability:
            # Only the quotation delimiters change. Apostrophes inside the
            # granted ability remain literal data for the ordinary compiler.
            start = ability.index(" has '") + len(' has ')
            if not ability.endswith("'."):
                return None
            ability = ability[:start] + '"' + ability[start + 1:-2] + '".'
        abilities.append(ability)
    text = '\n'.join((*abilities, equip_text))
    token = replace(record, name=name, mana_cost='', mana_value=0,
        type_line=characteristics['type_line'], oracle_text=text, power=None, toughness=None,
        loyalty=None, defense=None, colors=tuple(characteristics['colors']), keywords=(),
        produced_mana=(), layout='normal', faces=(), raw={})
    ir = compile_oracle_card(token, capability_registry=capability_registry,
        capability_profile=capability_profile)
    if ir.status != 'exact' or len(ir.faces) != 1:
        return None
    originals = tuple(ir.faces[0].nodes)
    fragments, children, aliases = [], [], {}
    for slot, original in enumerate(originals, 1):
        child_id = f'{node_id}:token-ability:{slot}'
        child = replace(original, node_id=child_id, span=span)
        if original.span.line == len(abilities) + 1 and original.kind == 'activated_ability':
            # Only this final source line is the literal Equip keyword expansion.
            # An identical quoted attachment activation stays an ordinary ability.
            from .granted_keyword_activation_nodes import equip_keyword_catalog_node
            child = equip_keyword_catalog_node(child, cost_text=equip_cost,
                registry=capability_registry, profile=capability_profile)
            if child is None:
                return None
        if original.kind == 'static_ability' and original.handlers:
            child_id = f'{node_id}:token-static:{slot}'
            child = replace(child, kind='granted_static_ability', node_id=child_id)
            ability_id = generated_ability_id(kind=child.kind, face_id=face_id,
                line=span.line, static_declaration=True, node_id=child_id)
            fragments.append(ability_fragment_to_dict(StaticComponentSpec(f'{record.oracle_id}:{ability_id}')))
        elif original.kind in {'activated_ability', 'mana_ability', 'triggered_ability'}:
            plan = attached_granted_ability_plan(node=child, quoted_text=original.text,
                oracle_id=record.oracle_id, face_id=face_id, source_line=span.line,
                card_name=name, keywords=(), node_id=child_id)
            if plan is None:
                return None
            fragments.append(plan.fragment)
            if (original.kind == 'triggered_ability'
                    and CURRENT_ABILITY_FRAGMENT_COVERAGE in original.runtime_coverage):
                fragments.append(ability_fragment_to_dict(StaticComponentSpec(plan.semantic_key)))
            child = replace(child, kind=plan.node_kind)
        elif not original.kind.startswith('granted_'):
            return None
        old_id = generated_ability_id(kind=original.kind, face_id=ir.faces[0].face_id,
            line=original.span.line, static_declaration=bool(original.handlers), node_id=original.node_id)
        new_id = generated_ability_id(kind=child.kind, face_id=face_id,
            line=span.line, static_declaration=bool(child.handlers), node_id=child_id)
        if old_id is None or new_id is None:
            return None
        aliases[old_id] = new_id
        aliases[f'{record.oracle_id}:{old_id}'] = f'{record.oracle_id}:{new_id}'
        children.append(child)
    def rewrite(value):
        if isinstance(value, dict):
            return {key:rewrite(child) for key,child in value.items()}
        if isinstance(value, (tuple,list)):
            return [rewrite(child) for child in value]
        return aliases.get(value, value) if isinstance(value, str) else value
    return fragments, [replace(child, handlers=tuple(rewrite(dict(h)) for h in child.handlers))
        for child in children], []
