from __future__ import annotations

"""Fixed scheduled-player predicates and nontargeted result binding."""

from copy import deepcopy
import re

from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from .fixed_controller_effect_sequences import _clauses
from .event_result_value_templates import scheduled_fractional_life_instruction
from .affected_player_discard_templates import (
    FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC,
    FIXED_CONTROLLER_DISCARD_MECHANIC,
)

SCHEDULED_PLAYER_CAPABILITY = 'trigger.event.scheduled_player_result'
SCHEDULED_PLAYER_MECHANIC = 'scheduled-player-event-result'
SCHEDULED_PLAYER_VARIANTS = frozenset({'each_player_upkeep','each_opponent_upkeep','enchanted_object_controller_upkeep',
                                     'each_player_draw','each_opponent_draw','each_other_player_draw'})


def scheduled_player_binding_spec(text, *, card_name=None):
    match=re.fullmatch(r"At the beginning of each (?P<who>player|opponent|other player)['’]s (?P<step>upkeep|draw step), (?P<body>.+)",text,re.I)
    attached=re.fullmatch(r"At the beginning of the upkeep of enchanted (?P<type>creature|permanent|artifact|enchantment|land)['’]s controller, (?P<body>.+)",text,re.I)
    if match is None and attached is None:return None
    step='draw' if match is not None and match['step'].casefold()=='draw step' else 'upkeep'
    conditions=[{'field':'step','op':'eq','value':step}]
    if attached is not None:
        conditions.append({'field':'source_attachment_controller','op':'eq','value':'$context.player','required_type':attached['type'].casefold()})
        return FixedPublicEventBindingSpec(event='step.begin',variant='enchanted_object_controller_upkeep',body=attached['body'],
            template_id='fixed-counter-step-trigger-v1',mechanic='cr-603-handling-triggered-abilities',condition={'all':conditions},
            capabilities=(SCHEDULED_PLAYER_CAPABILITY,'attachment.reference.current_or_lki'))
    who=match['who'].casefold().replace(' ','_')
    if who=='other_player' and step!='draw':return None
    if who in {'opponent','other_player'}:conditions.append({'field':'player','op':'ne','value':'$source.controller'})
    return FixedPublicEventBindingSpec(event='step.begin',variant='each_'+who+'_'+step,body=match['body'],
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
    normalized=scheduled_fractional_life_instruction(body) if 'half' in body.casefold() else None
    if 'half' in body.casefold() and normalized is None:return None
    if normalized is None:
        normalized=re.sub(r'\b(?:that player|they)\b','you',body,flags=re.I)
        normalized=re.sub(r'\b(loses|gains|draws|mills|discards)\b',lambda m:m[0][:-1],normalized,flags=re.I)
    template,effects,schema,mechanics=effect_template(normalized,card_name=card_name)
    if template is None or schema is not None or not 1<=len(effects)<=2:return None
    clauses = _clauses(body) if len(effects) == 2 else (body,)
    if clauses is None or len(clauses) != len(effects):return None
    results=[]
    result_mechanics=[]
    player = '$context.player'
    for clause, effect in zip(clauses, effects):
        discard = effect.get('op') == 'choose_cards_apnap'
        if discard:
            if effect.get('actor') != '$controller' or effect.get('players') != ['$controller']:
                return None
        elif effect.get('op') not in {'life','lose_life','draw','mill'} or effect.get('player')!='$controller':return None
        subject = re.match(r'(that player|they|you)\b', clause, re.I)
        if subject is not None:
            player = '$controller' if subject[1].casefold() == 'you' else '$context.player'
        elif not re.match(r'(?:loses?|gains?|draws?|mills?|discards?)\b', clause, re.I):
            return None
        result=deepcopy(dict(effect))
        if discard:
            if player == '$context.player' and effect.get('count') not in {1, 2, 3}:
                return None
            result['players']=[player]
            result_mechanics.append(FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC if player == '$context.player' else FIXED_CONTROLLER_DISCARD_MECHANIC)
        else:
            result['player']=player
        results.append(result)
    return ('scheduled-player-fixed-result-v1',tuple(results),None,tuple(dict.fromkeys((
        *(m for m in mechanics if m not in {'fixed-controller-effect-sequence', 'closed-effect-program', 'bound-effect-program', FIXED_CONTROLLER_DISCARD_MECHANIC}),
        *result_mechanics,SCHEDULED_PLAYER_MECHANIC)))),True
