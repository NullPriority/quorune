"""Complete draw/life clauses with one independently bound public query player."""
from dataclasses import replace
from . import regex_working_set as re
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .query_characteristic_templates import query_characteristic_quantity
from ..query_effect_amount_model import PublicQueryAmountSpec,PublicQueryAmountError
from ..target_player_query_amount_model import TargetPlayerQueryAmountSpec,TARGET_PLAYER_QUERY_AMOUNT_MECHANIC

_COLORS={'white':'W','blue':'U','black':'B','red':'R','green':'G'}
_COLOR=r'(?:white|blue|black|red|green)'


def _quantity(subject,source_name):
    union=re.fullmatch(rf'(?P<first>{_COLOR}) and/or (?P<second>{_COLOR}) (?P<objects>creatures?|permanents?)',subject,re.I)
    if union:
        base=query_characteristic_quantity(union['objects']+' you control',source_name=source_name)
        if base is None:return None
        colors=tuple(sorted({_COLORS[union['first'].casefold()],_COLORS[union['second'].casefold()]}))
        return replace(base,query=replace(base.query,colors_any=colors))
    return query_characteristic_quantity(subject+' you control',source_name=source_name)


def target_player_query_amount_template(text,*,compile_fixed,source_name):
    body=re.sub(r'^Domain\s*[—-]\s*','',text.strip(),flags=re.I)
    opponent=re.fullmatch(rf'(?P<result>Draw a card|You gain (?P<n>{FIXED_COUNT_PATTERN}) life) for each (?P<objects>.+?) target opponent controls\.',body,re.I)
    recipient=re.fullmatch(r'Target player draws a card for each (?P<objects>.+?) they control\.',body,re.I)
    match=opponent or recipient
    if match is None:return None
    coefficient=fixed_number(opponent['n']) if opponent and opponent['n'] is not None else 1
    if coefficient<=0:return None
    quantity=_quantity(match['objects'],source_name)
    if quantity is None:return None
    try:amount=TargetPlayerQueryAmountSpec(PublicQueryAmountSpec(quantity,coefficient))
    except (ValueError,TypeError):return None
    fixed=('You gain 1 life.' if opponent and opponent['n'] is not None else 'Draw a card.') if opponent else 'Target player draws a card.'
    compiled=compile_fixed(fixed)
    if compiled[0] is None or len(compiled[1])!=1:return None
    effect=dict(compiled[1][0]);slot='count' if effect.get('op')=='draw' else 'delta'
    if effect.get(slot)!=1:return None
    effect[slot]=amount.to_dict()
    schema={'zones':['player'],'categories':['player'],'player_relation':'opponent' if opponent else 'any','count':1}
    mechanics=tuple(dict.fromkeys((TARGET_PLAYER_QUERY_AMOUNT_MECHANIC,'cr-115-targets',*compiled[3])))
    return 'target-player-public-query-result-v1',(effect,),schema,mechanics
