from __future__ import annotations

"""Source-spanned fixed nonmana Morph declarations on otherwise complete cards."""

import re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..creature_subtypes import canonical_creature_subtype
from ..nonmana_morph_model import (
    FixedMorphObjectCost, FixedNonmanaMorphSpec, MorphObjectCostKind,
    NONMANA_MORPH_CAPABILITY, NONMANA_MORPH_HANDLER,
)
from .static_runtime_nodes import runtime_handler_node


def fixed_nonmana_morph_spec(material_line):
    match = re.fullmatch(r'Morph[—-]\s*(?P<cost>.+)\.', material_line.strip(), re.I)
    if match is None:
        return None
    text = match['cost']
    if life := re.fullmatch(r'Pay (?P<count>[1-9]\d?) life', text, re.I):
        cost = FixedMorphObjectCost(MorphObjectCostKind.LIFE, int(life['count']))
    elif reveal := re.fullmatch(r'Reveal a (?P<color>white|blue|black|red|green) card in your hand', text, re.I):
        color = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}[reveal['color'].lower()]
        cost = FixedMorphObjectCost(MorphObjectCostKind.REVEAL, color=color)
    elif discard := re.fullmatch(r'Discard a (?:(?P<subtype>[A-Za-z]+) )?card', text, re.I):
        subtype = canonical_creature_subtype(discard['subtype']) if discard['subtype'] else None
        if discard['subtype'] and subtype is None:
            return None
        cost = FixedMorphObjectCost(MorphObjectCostKind.DISCARD, subtype=subtype)
    elif returned := re.fullmatch(r'Return (?P<count>a|two) (?P<subtype>[A-Za-z]+) you control to (?:its|their) owner\'?s hand', text, re.I):
        subtype = returned['subtype'].lower()
        if subtype.endswith('s') and subtype not in {'plains'}:
            subtype = subtype[:-1]
        subtype = subtype if subtype in {'plains', 'island', 'swamp', 'mountain', 'forest'} else canonical_creature_subtype(subtype)
        if subtype is None:
            return None
        cost = FixedMorphObjectCost(MorphObjectCostKind.RETURN, 2 if returned['count'].lower() == 'two' else 1, subtype=subtype)
    else:
        return None
    return FixedNonmanaMorphSpec(cost)


def fixed_nonmana_morph_keyword_node(*, node_id, line, material_line, span, mechanics,
        capability_registry, capability_profile, residuals):
    if mechanics != ('morph',) or re.match(r'^Morph[—-]', material_line, re.I) is None:
        return None
    spec = fixed_nonmana_morph_spec(material_line)
    if spec is None:
        return None
    descriptor = {'handler_id': NONMANA_MORPH_HANDLER, 'schema_version': 1, 'event': 'morph.action',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'morph': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-nonmana-morph-v1', descriptor, (NONMANA_MORPH_CAPABILITY,)),
        kind='keyword_ability', event='morph.action', active_zone='all',
        runtime_coverage=('face_down_cast', 'face_down_characteristics', 'turn_face_up_special_action'),
        dependency_reason='Nonmana Morph requires the complete prospective face-up source',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
