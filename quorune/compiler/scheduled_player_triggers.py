from __future__ import annotations

"""Fixed scheduled-player predicates and nontargeted result binding."""

from copy import deepcopy
import re

from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec

SCHEDULED_PLAYER_CAPABILITY = 'trigger.event.scheduled_player_result'
SCHEDULED_PLAYER_MECHANIC = 'scheduled-player-event-result'
SCHEDULED_PLAYER_VARIANTS = frozenset({'each_player_upkeep','each_opponent_upkeep','enchanted_object_controller_upkeep'})


def scheduled_player_binding_spec(text, *, card_name=None):
    match=re.fullmatch(r"At the beginning of each (?P<who>player|opponent)['’]s upkeep, (?P<body>.+)",text,re.I)
    attached=re.fullmatch(r"At the beginning of the upkeep of enchanted (?P<type>creature|permanent|artifact|enchantment|land)['’]s controller, (?P<body>.+)",text,re.I)
    if match is None and attached is None:return None
    conditions=[{'field':'step','op':'eq','value':'upkeep'}]
    if attached is not None:
        conditions.append({'field':'source_attachment_controller','op':'eq','value':'$context.player','required_type':attached['type'].casefold()})
        return FixedPublicEventBindingSpec(event='step.begin',variant='enchanted_object_controller_upkeep',body=attached['body'],
            template_id='fixed-counter-step-trigger-v1',mechanic='cr-603-handling-triggered-abilities',condition={'all':conditions},
            capabilities=(SCHEDULED_PLAYER_CAPABILITY,'attachment.reference.current_or_lki'))
    if match['who'].casefold()=='opponent':conditions.append({'field':'player','op':'ne','value':'$source.controller'})
    return FixedPublicEventBindingSpec(event='step.begin',variant='each_'+match['who'].casefold()+'_upkeep',body=match['body'],
        template_id='fixed-counter-step-trigger-v1',mechanic='cr-603-handling-triggered-abilities',
        condition={'all':conditions},capabilities=(SCHEDULED_PLAYER_CAPABILITY,))


def scheduled_player_result(binding, body, *, effect_template, card_name):
    if binding.variant not in SCHEDULED_PLAYER_VARIANTS:return None
    if re.search(r'\b(?:that player|them)\b',body,re.I) and re.search(r'\bdeals? [1-9]\d* damage to (?:that player|them)\.',body,re.I):
        fixed=re.sub(r'\b(?:that player|them)\b','target player',body,flags=re.I)
        template,effects,schema,mechanics=effect_template(fixed,card_name=card_name)
        if template is None or len(effects)!=1 or schema is None or schema.get('zones')!=['player'] or schema.get('count')!=1:return None
        effect=deepcopy(dict(effects[0]))
        if effect.get('op')!='damage' or effect.get('target')!='$target.0':return None
        effect['target']='$context.player'
        return ('scheduled-player-fixed-damage-v1',(effect,),None,tuple(dict.fromkeys((
            *(m for m in mechanics if m!='cr-115-targets'),SCHEDULED_PLAYER_MECHANIC)))),True
    if not re.match(r'(?:that player|they)\b',body,re.I):return None
    normalized=re.sub(r'\b(?:that player|they)\b','you',body,flags=re.I)
    normalized=re.sub(r'\b(loses|gains|draws|mills)\b',lambda m:m[0][:-1],normalized,flags=re.I)
    template,effects,schema,mechanics=effect_template(normalized,card_name=card_name)
    if template is None or schema is not None or not 1<=len(effects)<=2:return None
    results=[]
    for effect in effects:
        if effect.get('op') not in {'life','lose_life','draw','mill'} or effect.get('player')!='$controller':return None
        result=deepcopy(dict(effect));result['player']='$context.player';results.append(result)
    return ('scheduled-player-fixed-result-v1',tuple(results),None,tuple(dict.fromkeys((
        *(m for m in mechanics if m!='fixed-controller-effect-sequence'),SCHEDULED_PLAYER_MECHANIC)))),True
