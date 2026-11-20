"""Complete self-source damage clauses with one public count for each player."""
from . import regex_working_set as re
from .query_characteristic_templates import query_characteristic_quantity
from ..query_effect_amount_model import PublicQueryAmountSpec
from ..fixed_damage_set_model import PlayerDamageRelation
from ..per_player_query_damage_model import PerPlayerQueryDamageSpec,PER_PLAYER_QUERY_DAMAGE_MECHANIC
from ..rules.source_references import SourceReferenceSpec


def per_player_query_damage_template(text,*,source_name,compile_fixed):
    match=re.fullmatch(r'(?P<source>.+?) deals damage to each (?P<players>player|opponent) equal to '
        r'(?P<twice>twice )?the number of (?P<objects>.+?) (?:they control|that player controls)\.',text.strip(),re.I)
    if match is None or not SourceReferenceSpec(source_name).matches(match['source']):return None
    quantity=query_characteristic_quantity(match['objects']+' you control',source_name=source_name)
    if quantity is None:return None
    relation=PlayerDamageRelation.ALL if match['players'].casefold()=='player' else PlayerDamageRelation.OPPONENTS
    try:spec=PerPlayerQueryDamageSpec(PublicQueryAmountSpec(quantity,2 if match['twice'] else 1),relation)
    except (TypeError,ValueError):return None
    fixed=compile_fixed(source_name+' deals 1 damage to each player.')
    if fixed[0] is None or len(fixed[1])!=1:return None
    effect=dict(fixed[1][0])
    if effect.get('op')!='damage_fixed_set' or effect.get('amount')!=1 or fixed[2] is not None:return None
    effect.update(amount=spec.to_dict(),groups=[{'kind':'players','relation':relation.value}])
    return 'per-player-public-query-damage-v1',(effect,),None,tuple(dict.fromkeys((PER_PLAYER_QUERY_DAMAGE_MECHANIC,*fixed[3])))
