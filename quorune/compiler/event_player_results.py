from __future__ import annotations

"""Closed fixed draw/life leaves addressed to sealed occurrence players."""

import re
from collections.abc import Mapping
from ..rules.event_subscriptions import FixedEventSubscriptionSet
from ..event_player_result_values import event_result_value_capabilities
from .event_result_value_templates import event_result_instruction

EVENT_PLAYER_RESULT_CAPABILITY='resolution.event_player.fixed_result'
EVENT_PLAYER_RESULT_MECHANIC='fixed-event-player-result'


def _damage_guarantees_player(condition):
    return isinstance(condition,Mapping) and (condition=={'field':'target_kind','op':'eq','value':'player'}
        or any(_damage_guarantees_player(child) for child in condition.get('all',())))


def _event_player(binding,subject):
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(binding.event_condition)
    except (TypeError,ValueError):return None
    occurrences=((binding.event.value,binding.event_condition),) if subscriptions is None else tuple(
        (entry.event,entry.condition) for entry in subscriptions.subscriptions)
    if binding.event.value not in {event for event,_condition in occurrences}:return None
    players=tuple(_occurrence_player(event,condition,binding.variant,subject) for event,condition in occurrences)
    return players[0] if players and players[0] is not None and all(player==players[0] for player in players) else None


def _occurrence_player(event,condition,variant,subject):
    if subject=='you':return '$controller'
    if subject.replace('\u2019', "'")=="that creature's controller":
        # CR 608.2h: an object-controller read is not an occurrence player.
        # Death supplies that departed incarnation's LKI; other events would
        # require a current/LKI object reference absent from this closed family.
        return '$context.previous_controller' if event=='creature.dies' else None
    if subject=='defending player':
        if event=='creature.attacks' and (variant=='source_attacks' or variant.startswith('public_attack_')):player='$context.defending_player'
        else:return None
    elif event=='card.discarded':player='$context.previous_controller'
    elif event in {'land.enter','creature.enter','permanent.enter','artifact.enter','enchantment.enter'}:player='$context.controller'
    elif event=='creature.dies':player='$context.previous_controller'
    elif event in {'spell.cast','card.drawn','player.scried','player.surveilled'}:
        player='$context.player'
    elif event in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(condition):
        player='$context.target'
    else:return None
    return player


def _fixed_event_player_clause(binding,body,*,effect_template,card_name):
    match=re.fullmatch(r"(?P<subject>that player|they|defending player|that creature['’]s controller|you) (?P<verb>draws?|loses?|gains?) (?P<body>.+)",body,re.I)
    if match is None:return None
    player=_event_player(binding,match['subject'].casefold())
    if player is None:return None
    verb=match['verb'].casefold().removesuffix('s')
    normalized=event_result_instruction(match['subject'].casefold(),verb,match['body'])
    if normalized is None:return None
    compiled=effect_template(normalized,card_name=card_name)
    if compiled[0] is None or compiled[2] is not None or len(compiled[1])!=1:return None
    effect=compiled[1][0];layouts={'draw':('count',{'op','player','count','private'}),
        'life':('delta',{'op','player','delta'}),'lose_life':('amount',{'op','player','amount'})}
    if effect.get('op') not in layouts:return None
    field,fields=layouts[effect['op']]
    if (set(effect)!=fields or effect.get('player')!='$controller'
            or event_result_value_capabilities(effect['op'],field,effect.get(field)) is None
            or effect['op']=='draw' and effect['private'] is not True):return None
    return {**effect,'player':player},compiled[3]


def fixed_event_player_result(binding,body,*,effect_template,card_name):
    from .fixed_controller_effect_sequences import _clauses
    clauses=_clauses(body) or (body,)
    lowered=tuple(_fixed_event_player_clause(binding,clause,effect_template=effect_template,card_name=card_name) for clause in clauses)
    if any(value is None for value in lowered) or not any(value[0]['player']!='$controller' for value in lowered):return None
    return ('fixed-event-player-result-v1',tuple(value[0] for value in lowered),None,
        tuple(dict.fromkeys((*[mechanic for value in lowered for mechanic in value[1]],EVENT_PLAYER_RESULT_MECHANIC)))),False
