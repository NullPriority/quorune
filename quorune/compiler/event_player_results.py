from __future__ import annotations

"""Closed fixed draw/life leaves addressed to sealed occurrence players."""

import re
from collections.abc import Mapping

EVENT_PLAYER_RESULT_CAPABILITY='resolution.event_player.fixed_result'
EVENT_PLAYER_RESULT_MECHANIC='fixed-event-player-result'


def _damage_guarantees_player(condition):
    return isinstance(condition,Mapping) and (condition=={'field':'target_kind','op':'eq','value':'player'}
        or any(_damage_guarantees_player(child) for child in condition.get('all',())))


def _event_player(binding,subject):
    if subject=='you':return '$controller'
    if subject=='defending player':
        if binding.event.value=='creature.attacks' and (binding.variant=='source_attacks' or binding.variant.startswith('public_attack_')):player='$context.defending_player'
        else:return None
    elif binding.event.value=='card.discarded':player='$context.previous_controller'
    elif binding.event.value in {'land.enter','creature.enter','permanent.enter','artifact.enter','enchantment.enter'}:player='$context.controller'
    elif binding.event.value=='creature.dies':player='$context.previous_controller'
    elif binding.event.value in {'spell.cast','card.drawn','player.scried','player.surveilled'}:
        player='$context.player'
    elif binding.event.value in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(binding.event_condition):
        player='$context.target'
    else:return None
    return player


def _fixed_event_player_clause(binding,body,*,effect_template,card_name):
    match=re.fullmatch(r"(?P<subject>that player|they|defending player|that creature['’]s controller|you) (?P<verb>draws?|loses?|gains?) (?P<body>.+)",body,re.I)
    if match is None:return None
    player=_event_player(binding,match['subject'].casefold())
    if player is None:return None
    verb=match['verb'].casefold().removesuffix('s')
    normalized=verb.capitalize()+' '+match['body']
    compiled=effect_template(normalized,card_name=card_name)
    if compiled[0] is None or compiled[2] is not None or len(compiled[1])!=1:return None
    effect=compiled[1][0];layouts={'draw':('count',{'op','player','count','private'}),
        'life':('delta',{'op','player','delta'}),'lose_life':('amount',{'op','player','amount'})}
    if effect.get('op') not in layouts:return None
    field,fields=layouts[effect['op']]
    if (set(effect)!=fields or effect.get('player')!='$controller' or type(effect.get(field)) is not int
            or effect[field]<=0 or effect['op']=='draw' and effect['private'] is not True):return None
    return {**effect,'player':player},compiled[3]


def fixed_event_player_result(binding,body,*,effect_template,card_name):
    from .fixed_controller_effect_sequences import _clauses
    clauses=_clauses(body) or (body,)
    lowered=tuple(_fixed_event_player_clause(binding,clause,effect_template=effect_template,card_name=card_name) for clause in clauses)
    if any(value is None for value in lowered) or not any(value[0]['player']!='$controller' for value in lowered):return None
    return ('fixed-event-player-result-v1',tuple(value[0] for value in lowered),None,
        tuple(dict.fromkeys((*[mechanic for value in lowered for mechanic in value[1]],EVENT_PLAYER_RESULT_MECHANIC)))),False
