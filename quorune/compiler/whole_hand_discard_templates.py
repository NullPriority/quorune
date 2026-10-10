from __future__ import annotations

"""Complete mandatory whole-hand discard instructions and fixed draw tails."""

import re
from copy import deepcopy
from ..whole_hand_discard_model import WHOLE_HAND_DISCARD_OPERATION,WHOLE_HAND_DISCARD_MECHANIC
from .draw_templates import fixed_draw_effect_template


def whole_hand_discard_effect_template(text):
    body=' '.join(text.strip().split())
    match=re.fullmatch(r'(?:(?P<subject>Each player|Each opponent|Target player|Target opponent|You) )?'
        r'discards? (?P<hand>their|your)(?: entire)? hands?\.',body,re.I)
    if match is None:
        return None
    subject=(match['subject'] or 'You').casefold()
    if (subject=='you')!=(match['hand'].casefold()=='your'):
        return None
    players={'you':['$controller'],'each player':'all','each opponent':'opponents'}.get(subject,['$target.0'])
    targeted=subject.startswith('target')
    schema={'zones':['player'],'categories':['player'],'player_relation':'opponent' if subject=='target opponent' else 'any','count':1} if targeted else None
    return ('whole-hand-discard-v1',({'op':WHOLE_HAND_DISCARD_OPERATION,'actor':'$controller','players':players},),schema,
        (WHOLE_HAND_DISCARD_MECHANIC,'cr-402-hand',*(('cr-115-targets',) if targeted else ())))


def whole_hand_discard_draw_sequence_template(text):
    match=re.fullmatch(r'(?P<discard>(?:(?:Each player|Each opponent|Target player|Target opponent|You) )?'
        r'discards? (?:their|your)(?: entire)? hands?)(?:, then| and|\. Then) (?P<draw>draws? .+)\.',text.strip(),re.I)
    if match is None:
        return None
    discard=whole_hand_discard_effect_template(match['discard']+'.')
    if discard is None:
        return None
    players=discard[1][0]['players'];draw_text=match['draw']+'.'
    if players=='all':draw_text='Each player '+draw_text.lower()
    elif players==['$controller']:draw_text=re.sub(r'^draws\b','Draw',draw_text,flags=re.I)
    else:return None
    draw=fixed_draw_effect_template(draw_text)
    if draw is None or draw[2] is not None or len(draw[1])!=1 or draw[1][0]['op'] not in {'draw','draw_each_player'}:
        return None
    return ('whole-hand-discard-fixed-draw-sequence-v1',(*discard[1],*draw[1]),None,
        tuple(dict.fromkeys(('closed-effect-program',*discard[3],*draw[3]))))


def whole_hand_discard_public_draw_sequence(text,*,source_name):
    match=re.fullmatch(r'(?P<discard>(?:You )?discard your(?: entire)? hand)(?:, then|\. Then) (?P<draw>draw .+)\.',text.strip(),re.I)
    if match is None:
        return None
    discard=whole_hand_discard_effect_template(match['discard']+'.')
    from .public_query_effect_amounts import public_query_effect_amount_template
    from .scalar_effect_amounts import scalar_effect_amount_template
    from .declared_effect_amounts import declared_effect_amount_template
    def fixed(body):return fixed_draw_effect_template(body) or (None,(),None,())
    tail=match['draw']+'.'
    tail=re.sub(r'^draw a card for each card (you(?:\'ve)? discarded this turn)\.$',
        lambda m:'Draw cards equal to the number of cards '+m[1]+'.',tail,flags=re.I)
    draw=public_query_effect_amount_template(tail,source_name=source_name,compile_fixed=fixed) or scalar_effect_amount_template(
        tail,source_name=source_name,compile_fixed=fixed) or declared_effect_amount_template(tail,source_name=source_name,compile_fixed=fixed)
    if discard is None or draw is None or len(draw[1])!=1 or draw[1][0].get('op')!='draw' or draw[2] is not None:
        return None
    return ('whole-hand-discard-public-draw-sequence-v1',(*discard[1],*deepcopy(draw[1])),None,
        tuple(dict.fromkeys(('closed-effect-program',*discard[3],*draw[3]))))
