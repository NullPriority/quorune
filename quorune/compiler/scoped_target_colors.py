"""Fixed-color scopes and exact same-target companion instructions."""
from dataclasses import replace
import re

from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION
from ..resolution_color_model import SCOPED_TARGET_COLOR_VERSION,TARGET_COLOR_SET_VERSION,TARGET_COLOR_SCOPE_MECHANIC
from ..target_cardinality import AnnouncedTargetCardinality,selected_target_count_descriptor
from ..targets import target_plan
from .direct_target import direct_permanent_target_spec
from .fixed_target_color import fixed_target_color_template
from .fixed_effect_clause_sequences import _period_sentence_clauses,_top_level_scope_text
from .draw_templates import fixed_draw_effect_template
from .tap_state_templates import targeted_tap_state_effect_template

_COLORS={'white':'W','blue':'U','black':'B','red':'R','green':'G','colorless':None}


def _schema(subject):
    if subject=='spell':return {'zones':['stack'],'categories':['spell'],'count':1}
    spec=direct_permanent_target_spec('target '+subject)
    return spec.to_target_schema() if spec is not None else None


def _same_target(a,b):
    try:
        pa,pb=target_plan(a),target_plan(b)
        return len(pa.groups)==len(pb.groups)==1 and replace(pa.groups[0],group_id='same')==replace(pb.groups[0],group_id='same')
    except (ValueError,TypeError):return False


def _color_clause(text,compile_leaf):
    material=' '.join(text.strip().rstrip('.').split())
    prefixed=material.casefold().startswith('until end of turn, ')
    if prefixed:material=material[len('Until end of turn, '):]
    temporary=material.casefold().endswith(' until end of turn')
    if temporary:material=material[:-len(' until end of turn')]
    match=re.fullmatch(r'Target (?P<subject>creature|permanent|spell) (?P<body>.+)',material,re.I)
    if match is None:return None
    body=match['body'];color=re.search(r'\bbecomes (?P<color>white|blue|black|red|green|colorless)(?P<add> in addition to its other colors)?(?P<time> until end of turn)?\b',body,re.I)
    if color is None:return None
    schema=_schema(match['subject'].casefold())
    if schema is None:return None
    key=_COLORS[color['color'].casefold()]
    if color['add'] and key is None:return None
    duration='until_end_of_turn' if temporary or prefixed or color['time'] else 'zone_object'
    effect={'op':CHARACTERISTIC_OPERATION,'schema_version':SCOPED_TARGET_COLOR_VERSION,'card':'$target.0',
        'colors':[key] if key else [],'color_mode':'add' if color['add'] else 'set','duration':duration}
    remainder=body[:color.start()]+body[color.end():]
    remainder=re.sub(r',\s*,',',',remainder).strip(' ,')
    remainder=re.sub(r'^and\s+|\s+and$','',remainder,flags=re.I)
    remainder=re.sub(r',\s*(?:and\s+)?(?=gains?\b|gets?\b)',' and ',remainder,flags=re.I).strip()
    effects=[effect];mechanics=[TARGET_COLOR_SCOPE_MECHANIC,'cr-611-continuous-effects','cr-115-targets']
    if remainder:
        suffix=' until end of turn.' if duration=='until_end_of_turn' and ' this turn' not in remainder.casefold() else '.'
        other=compile_leaf('Target '+match['subject']+' '+remainder+suffix)
        if other is None or other[0] is None or not _same_target(schema,other[2]):return None
        effects.extend(other[1]);mechanics.extend(other[3])
    return effects,schema,mechanics


def scoped_target_color_template(text,*,compile_leaf):
    if fixed_target_color_template(text) is not None:return None
    material=' '.join(text.strip().split())
    many=re.fullmatch(r'One or more target creatures become (white|blue|black|red|green|colorless) until end of turn\.',material,re.I)
    if many:
        color=_COLORS[many[1].casefold()];schema=_schema('creature');schema.pop('count')
        schema['count_parameter']=AnnouncedTargetCardinality(selection='one_or_more',parameter='any_number',schema_version=3).to_dict()
        return ('fixed-minimum-one-color-set-v1',({'op':CHARACTERISTIC_OPERATION,'schema_version':TARGET_COLOR_SET_VERSION,
            'cards':'$targets','maximum_targets':selected_target_count_descriptor(),'announced_target_count':True,
            'colors':[color] if color else [],'color_mode':'set','duration':'until_end_of_turn'},),schema,
            (TARGET_COLOR_SCOPE_MECHANIC,'cr-611-continuous-effects','cr-115-targets'))
    if 'becomes ' not in material.casefold() or len(re.findall(r'\btarget\b',_top_level_scope_text(material),re.I))!=1:return None
    clauses=_period_sentence_clauses(material) or (material,)
    effects=[];schema=None;mechanics=[];has_color=False
    for clause in clauses:
        normalized=clause
        if schema is not None:normalized=re.sub(r'\bthat creature\b','target creature',normalized,flags=re.I)
        color=_color_clause(normalized,compile_leaf)
        if color:
            ce,cs,cm=color;has_color=True
        else:
            if len(clauses)==1:return None
            other=compile_leaf(normalized)
            if other is None or other[0] is None:
                tap=targeted_tap_state_effect_template(normalized)
                other=tap.compiled() if tap is not None else fixed_draw_effect_template(normalized)
            if other is None or other[0] is None:return None
            _,ce,cs,cm=other
        if cs is not None:
            if schema is not None and not _same_target(schema,cs):return None
            schema=cs
        effects.extend(ce);mechanics.extend(cm)
    if not has_color or schema is None:return None
    return ('fixed-scoped-target-color-v1',tuple(effects),schema,tuple(dict.fromkeys(mechanics)))
