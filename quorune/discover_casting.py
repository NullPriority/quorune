from __future__ import annotations

"""Current Discover permission over canonical exile casting and zone owners."""

from .discover_model import DiscoverProgress,discover_spell_qualifies,discover_options_fingerprint
from .errors import GameRuleError,StateInvariantError
from .replacement.immutable import thaw_value
from .rules.casting.face_queries import prospective_cast_card
from .rules.casting.proposal import _static_cast_rule_state
from .rules.resolving_cast_payments import commit_resolving_free_exile_cast


def require_discover_resolution(host,*,actor,stack_ref):
    item=next((item for item in host.state.stack if item.ref==stack_ref),None)
    if item is None or item.controller!=actor or not item.context.get('currently_resolving'):
        raise GameRuleError('The current Discover resolution changed')
    return item


def require_discover_exile(host,progress,*,actor,include_candidate=True):
    cards=[]
    for identity in progress.exiled if include_candidate else progress.cleanup_cards:
        card=host.state.cards.get(identity.object_id)
        if (card is None or card.ref!=identity.ref or card.logical_object_id!=identity.logical_object_id
                or card.owner!=actor or card.zone!='exile' or not card.is_card_object):
            raise GameRuleError('A Discover exile incarnation changed')
        cards.append(card)
    return tuple(cards)


def _discover_cast_option_pairs(host,*,item,actor,effect):
    if item is None:return ()
    require_discover_resolution(host,actor=actor,stack_ref=item.ref)
    progress=DiscoverProgress.from_dict(effect['progress'])
    require_discover_exile(host,progress,actor=actor)
    if progress.candidate is None:return ()
    card=host.state.cards[progress.candidate.object_id];record=host.card_record(card)
    options=host._one_shot_exile_cast_options(actor=actor,card=card,maximum_mana_value=None)
    result=[]
    for option in options:
        if not discover_spell_qualifies(progress.amount,mana_value=option['mana_value']):continue
        face_name=option.get('face')
        face=next((face for face in host._cast_faces(record,required_face=face_name)
                   if face_name is not None and face.get('name')==face_name),None)
        prohibition,_timing=_static_cast_rule_state(host,actor,prospective_cast_card(host,card,face_name),
            face=face,face_name=face_name,face_down=False)
        if prohibition is None:
            public={**option,'id':'discover:'+discover_options_fingerprint((option,))}
            result.append((public,option))
    return tuple(result)


def discover_cast_options(host,*,item,actor,effect):
    return tuple(public for public,_canonical in _discover_cast_option_pairs(host,item=item,actor=actor,effect=effect))


def execute_discover_cast(host,intent):
    item=require_discover_resolution(host,actor=intent.actor,stack_ref=intent.stack_ref)
    pairs=_discover_cast_option_pairs(host,item=item,actor=intent.actor,effect={'progress':intent.progress.to_dict()})
    options=tuple(public for public,_canonical in pairs)
    if discover_options_fingerprint(options)!=intent.options_fingerprint:
        raise GameRuleError('Discover cast options changed')
    response=dict(thaw_value(intent.response));face=response.get('face');selected=response.get('cost_option')
    matches=[(public,canonical) for public,canonical in pairs if (selected is None or selected==public['id'])
             and (face is None or face==public.get('face'))]
    if len(matches)!=1:raise GameRuleError('Select one current Discover casting option')
    _public,option=matches[0];card=host.state.cards[intent.progress.candidate.object_id]
    response.update({'card':card.ref,'from':'exile','auto_pay':True,'cost_option':option['id']})
    if option.get('face') is not None:response['face']=option['face']
    before={stack.ref for stack in host.state.stack}
    commit_resolving_free_exile_cast(host,actor=intent.actor,response=response,
        payment_id=f'{intent.stack_ref}:discover:{intent.progress.candidate.logical_object_id}',
        replacement_selections=intent.replacement_selections)
    if not any(stack.ref not in before and stack.kind=='spell' and stack.card_object_id==card.object_id
               for stack in host.state.stack):
        raise StateInvariantError('The Discover choice created no spell')
