"""Closed additive subtype and quoted-ability composition."""

import re

from ..creature_subtypes import canonical_creature_subtype_surface
from ..object_predicate import ObjectQuerySpec
from ..type_subtype_vocabulary import CARD_TYPE_SUBTYPES
from .continuous_templates import _attached_modifier
from .oracle_source_text import without_ability_word_label
from .public_state_queries import fixed_characteristic_battlefield_query_subject


def subtype_composite_shell(text):
    text = without_ability_word_label(' '.join(text.strip().split()))
    if text is None:
        return None
    match = re.fullmatch(r'(?P<subject>.+?) (?:is|are) (?:an? )?(?P<subtype>[A-Za-z][A-Za-z\'-]*(?: Lord)?) '
        r'in addition to (?:its|their) other (?P<family>creature )?types and (?:has|have) (?P<quotes>.+?)\.?', text, re.I)
    if match is None or re.fullmatch(r'"[^"]+"(?:\s*(?:,\s*(?:and\s+)?|and\s+)"[^"]+"){0,3}', match['quotes']) is None:
        return None
    subject = re.sub(r'^Each (creature|artifact|land|permanent)\b', lambda m: m[1].title() + 's', match['subject'], flags=re.I)
    attached = re.fullmatch(r'(?:Enchanted|Equipped) (artifact|creature|permanent)', subject, re.I)
    if attached:
        predicate = ObjectQuerySpec(zones=('battlefield',), types_all=() if attached[1].casefold() == 'permanent' else (attached[1].casefold(),))
        target = {'kind': 'attached', 'controller': None, 'exclude_source': False, 'predicate': predicate.to_dict()}
    else:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None:
            return None
        relation, predicate, excluded = parsed
        target = {'kind': 'fixed_query', 'controller': relation, 'exclude_source': excluded, 'predicate': predicate.to_dict()}
    subtype = canonical_creature_subtype_surface(match['subtype'])
    if 'artifact' in predicate.types_all:
        surface = match['subtype'].casefold()
        subtype = next((value for value in CARD_TYPE_SUBTYPES['artifact'] if surface in {value, value + 's'}), subtype)
    if match['family'] and subtype not in CARD_TYPE_SUBTYPES['creature']:
        return None
    if subtype is None:
        return None
    modifier = _attached_modifier()
    modifier['type_operations'] = [{'op': 'add_types', 'field': 'subtypes', 'values': [subtype.title()]}]
    return target, modifier, tuple(re.findall(r'"([^"]+)"', match['quotes']))
