"""Fixed printed-order atomic clauses sharing one targeted player."""
import re
from .draw_templates import fixed_draw_effect_template
from .mill_templates import fixed_mill_effect_template
from .life_templates import fixed_life_effect_template
from .affected_player_discard_templates import (
    fixed_affected_player_discard_effect_template, fixed_affected_player_hand_exile_effect_template,
)
from .counter_placement_templates import fixed_player_counter_placement_effect_template

TARGET_PLAYER_SEQUENCE_CAPABILITY='resolution.effect.fixed_target_player_sequence'
TARGET_PLAYER_SEQUENCE_MECHANIC='fixed-target-player-effect-sequence'


def _leaf(text):
    for compiler in (fixed_draw_effect_template,fixed_mill_effect_template,fixed_life_effect_template,
            fixed_affected_player_discard_effect_template,fixed_affected_player_hand_exile_effect_template,
            fixed_player_counter_placement_effect_template):
        value=compiler(text)
        if value is not None:return value if isinstance(value,tuple) else value.compiled()
    return None


def fixed_target_player_sequence_template(text):
    normalized=' '.join(text.strip().split())
    match=re.fullmatch(r'Target (?P<relation>player|opponent) (?P<body>.+)',normalized,re.I)
    if match is None or any(token in normalized for token in ('"','(',')','{')):return None
    verbs = r"(?:draws|mills|discards|exiles|loses|gains|gets)"
    parts = re.split(
        rf",?\s+(?:and|then)\s+(?={verbs}\b)|,\s+(?={verbs}\b)|\.\s+(?=You (?:gain|lose)\b)",
        match["body"].replace(", and ", " and "), flags=re.I,
    )
    if not 2<=len(parts)<=4:return None
    relation=match['relation'].casefold();effects=[];mechanics=[];schema=None
    for part in parts:
        clause=part.strip().rstrip('.')+'.'
        controller=bool(re.match(r'You (?:gain|lose)\b',clause,re.I))
        if not controller:clause='Target '+relation+' '+clause
        compiled=_leaf(clause)
        if compiled is None or len(compiled[1])!=1:return None
        if controller:
            if compiled[2] is not None or compiled[1][0].get('op')!='life' or compiled[1][0].get('player')!='$controller':return None
        else:
            child_schema={**compiled[2],'player_relation':compiled[2].get('player_relation','any')} if compiled[2] is not None else None
            if child_schema is None or schema is not None and schema!=child_schema:return None
            schema=child_schema
        effects.extend(compiled[1]);mechanics.extend(compiled[3])
    return ('fixed-target-player-effect-sequence-v1',tuple(effects),schema,
        tuple(dict.fromkeys((TARGET_PLAYER_SEQUENCE_MECHANIC,*mechanics))))
