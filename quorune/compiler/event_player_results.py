from __future__ import annotations

"""Closed fixed draw/life leaves addressed to sealed occurrence players."""

import re
from collections.abc import Mapping

EVENT_PLAYER_RESULT_CAPABILITY='resolution.event_player.fixed_result'
EVENT_PLAYER_RESULT_MECHANIC='fixed-event-player-result'


def _damage_guarantees_player(condition):
    return isinstance(condition,Mapping) and (condition=={'field':'target_kind','op':'eq','value':'player'}
        or any(_damage_guarantees_player(child) for child in condition.get('all',())))


def fixed_event_player_result(binding,body,*,effect_template,card_name):
    match=re.fullmatch(r'(?P<subject>that player|they|defending player) (?P<verb>draws?|loses?|gains?) (?P<body>.+)',body,re.I)
    if match is None:return None
    player=None
    if match['subject'].casefold()=='defending player':
        if binding.event.value=='creature.attacks' and binding.variant=='source_attacks':player='$context.defending_player'
    elif binding.event.value=='card.discarded':player='$context.previous_controller'
    elif binding.event.value in {'spell.cast','card.drawn','player.scried','player.surveilled'}:
        player='$context.player'
    elif binding.event.value in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(binding.event_condition):
        player='$context.target'
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
    return ('fixed-event-player-result-v1',({**effect,'player':player},),None,
        (*compiled[3],EVENT_PLAYER_RESULT_MECHANIC)),False
