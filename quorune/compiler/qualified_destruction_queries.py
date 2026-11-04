"""Fixed destruction selections expressed by existing immutable query fields."""
import re
from dataclasses import replace
from ..object_predicate import ObjectQuerySpec
from ..target_numeric import TargetNumericCharacteristic,TargetNumericCharacteristicSpec,TargetNumericComparison
from .noncreature_quantity_subtypes import noncreature_quantity_subtype_fields


def qualified_destruction_query(phrase,*,parse_base):
    numeric=re.fullmatch(r'(?P<body>creatures) with (?P<stat>power|toughness) (?P<n>[0-9]+) or (?P<bound>greater|less)',phrase)
    if numeric:
        return ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),
            numeric_characteristic=TargetNumericCharacteristicSpec(TargetNumericCharacteristic(numeric['stat']),
                TargetNumericComparison.AT_LEAST if numeric['bound']=='greater' else TargetNumericComparison.AT_MOST,int(numeric['n'])))
    commander=re.fullmatch(r'(?P<body>creatures(?: and planeswalkers)?) except (?:for )?commanders',phrase)
    if commander:
        parsed=parse_base(commander['body'])
        return replace(parsed[0],commander=False) if parsed is not None else None
    if phrase=='non-aura enchantments':
        return ObjectQuerySpec(zones=('battlefield',),types_all=('enchantment',),excluded_subtypes=('aura',))
    if phrase=='nonbasic lands':
        return ObjectQuerySpec(zones=('battlefield',),types_all=('land',),excluded_supertypes=('basic',))
    color=re.fullmatch(r'non(?P<color>white|blue|black|red|green) creatures',phrase)
    if color:
        return ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),
            colors_none=({'white':'W','blue':'U','black':'B','red':'R','green':'G'}[color['color']],))
    parts=phrase.split(' and ')
    if len(parts)<=2 and len(set(parts))==len(parts) and all(part in {'auras','equipment'} for part in parts):
        fields=[noncreature_quantity_subtype_fields(part,zone='battlefield',definition_extensions=False) for part in parts]
        if all(fields) and all(set(f.get('types_all',f.get('types_any',())))<={'artifact','enchantment','land','planeswalker','battle'} for f in fields):
            queries=tuple(ObjectQuerySpec(zones=('battlefield',),**f) for f in fields)
            return queries[0] if len(queries)==1 else ObjectQuerySpec(zones=('battlefield',),any_of=queries)
    return None
