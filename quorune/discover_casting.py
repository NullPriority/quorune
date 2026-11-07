from __future__ import annotations

"""Current resolving-cast permission over canonical exile casting and zones."""

from .discover_model import (ExileCastProgress, discover_spell_qualifies, discover_options_fingerprint,
    QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_STEP_OPERATION, validate_qualified_scan_effect)
from .errors import GameRuleError,StateInvariantError
from .replacement.immutable import thaw_value
from .rules.casting.face_queries import prospective_cast_card
from .rules.casting.proposal import _static_cast_rule_state
from .rules.resolving_cast_payments import commit_resolving_free_exile_cast


def qualified_scan_library_refs(host, *, actor, effect):
    """Expose the next supplied identity solely to the server's scan snapshot."""
    operation = effect.get('op')
    if operation not in {QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_STEP_OPERATION}:
        return {}
    if effect.get('player') != actor:
        raise StateInvariantError('The supplied scan controller changed')
    if operation == QUALIFIED_SCAN_OPERATION:
        validate_qualified_scan_effect(effect, resolved=True)
        supplier = effect['supplier']
    else:
        if (set(effect) != {'op', 'schema_version', 'player', 'progress', 'pending'}
                or type(effect['schema_version']) is not int or effect['schema_version'] != 1):
            raise StateInvariantError('The supplied scan step changed')
        progress = ExileCastProgress.from_dict(effect['progress'])
        if progress.step_operation != operation:
            raise StateInvariantError('The supplied scan progress changed its search rule')
        supplier = progress.supplier
    if supplier not in host.state.players:
        raise StateInvariantError('The supplied scan names an unknown player')
    if supplier not in host.active_seats:
        return {}
    library = host.state.players[supplier].zones['library']
    return {supplier: [host.state.cards[library[-1]].ref] if library else []}


def require_discover_resolution(host,*,actor,stack_ref):
    item=next((item for item in host.state.stack if item.ref==stack_ref),None)
    if item is None or item.controller!=actor or not item.context.get('currently_resolving'):
        raise GameRuleError('The current Discover resolution changed')
    return item


def require_discover_exile(host,progress,*,actor,include_candidate=True):
    cards=[]
    owner = progress.supplier or actor
    for identity in progress.exiled if include_candidate else progress.cleanup_cards:
        card=host.state.cards.get(identity.object_id)
        if (card is None or card.ref!=identity.ref or card.logical_object_id!=identity.logical_object_id
                or card.owner!=owner or card.zone!='exile' or not card.is_card_object):
            raise GameRuleError('A Discover exile incarnation changed')
        cards.append(card)
    return tuple(cards)


def _discover_cast_option_pairs(host,*,item,actor,effect):
    if item is None:return ()
    require_discover_resolution(host,actor=actor,stack_ref=item.ref)
    progress=ExileCastProgress.from_dict(effect['progress'])
    require_discover_exile(host,progress,actor=actor)
    if progress.candidate is None:return ()
    card=host.state.cards[progress.candidate.object_id];record=host.card_record(card)
    options=host._one_shot_exile_cast_options(actor=actor,card=card,maximum_mana_value=None)
    result=[]
    if progress.supplier is not None:
        prefix = 'exile-until-instant-or-sorcery:'
    else:
        prefix = 'exile-until-nonland:' if progress.amount is None else 'discover:'
    for option in options:
        if progress.amount is not None and not discover_spell_qualifies(progress.amount, mana_value=option['mana_value']):
            continue
        face_name=option.get('face')
        face=next((face for face in host._cast_faces(record,required_face=face_name)
                   if face_name is not None and face.get('name')==face_name),None)
        prohibition,_timing=_static_cast_rule_state(host,actor,prospective_cast_card(host,card,face_name),
            face=face,face_name=face_name,face_down=False)
        if prohibition is None:
            public={**option,'id':prefix+discover_options_fingerprint((option,))}
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
    if intent.progress.supplier is not None:
        producer = 'exile-until-instant-or-sorcery'
    else:
        producer = 'exile-until-nonland' if intent.progress.amount is None else 'discover'
    commit_resolving_free_exile_cast(host,actor=intent.actor,response=response,
        payment_id=f'{intent.stack_ref}:{producer}:{intent.progress.candidate.logical_object_id}',
        replacement_selections=intent.replacement_selections)
    if not any(stack.ref not in before and stack.kind=='spell' and stack.card_object_id==card.object_id
               for stack in host.state.stack):
        raise StateInvariantError('The Discover choice created no spell')
