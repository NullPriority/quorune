"""Current same-name revealed-library casting through canonical offer/payment owners."""
from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError,StateInvariantError
from ..replacement.immutable import thaw_value
from ..ripple_model import RippleSpec
from .ripple import RIPPLE_FIELD,RIPPLE_CAST_CONTEXT,ripple_program_dependencies
from .ripple_resolution_model import RIPPLE_STATE_FIELD,RippleResolutionState
from .casting.face_queries import current_printed_cast_mana_cost, offered_cast_methods, prospective_cast_card
from .revealed_library_cast import MANA_VALUE_CAST_CONTEXT, MANA_VALUE_CAST_FIELD
from .casting.proposal import build_cast_offer
from .resolving_cast_payments import commit_resolving_cast


def _live_item(host,actor,stack_ref,state):
    item=next((i for i in host.state.stack if i.ref==stack_ref),None)
    context_field = MANA_VALUE_CAST_CONTEXT if state.casts_once else RIPPLE_CAST_CONTEXT
    if (item is None or actor not in host.active_seats or item.controller != actor
            or (state.casts_once and item.kind != "triggered_ability")
            or not item.context.get('currently_resolving')
            or state.stack_ref != stack_ref or state.actor != actor
            or item.context.get(context_field) != state.source.to_dict()):
        raise GameRuleError("The revealed-library cast lost its original trigger or cast facts")
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program) or not ripple_program_dependencies(program):
        raise GameRuleError("The revealed-library cast permission is no longer current and trusted")
    effect, = program.effects
    correct_scope = (MANA_VALUE_CAST_FIELD in effect if state.casts_once else
                     RIPPLE_FIELD in effect and RippleSpec.from_dict(effect[RIPPLE_FIELD]) == state.spec)
    if not correct_scope:
        raise GameRuleError("The revealed-library cast changed its original instruction")
    return item


def _qualifies_mana_value(host, card, record, face, maximum):
    from ..selection.exile_cast import mana_value_of_cost

    view = prospective_cast_card(host, card, face)
    cost = current_printed_cast_mana_cost(host, view, record)
    return mana_value_of_cost(cost, x_value=0) <= maximum


def _option_pairs(host,actor,state):
    from ..carddb_characteristics import public_revealed_library_rules_names
    pairs=[]
    for identity in state.looked:
        card=host.state.cards.get(identity.object_id)
        if (card is None or card.zone!='library' or card.ref!=identity.ref
                or card.logical_object_id!=identity.logical_object_id):
            continue
        if card.owner!=actor:raise GameRuleError('Ripple cannot cast another player library card')
        record=host.card_record(card)
        if record is None:
            continue
        if state.casts_once:
            if not card.is_card_object or card.face_down or not set(host.seats) <= set(card.revealed_to):
                raise GameRuleError("The selected library choice needs the original publicly revealed card")
        elif not state.source.matches_names(tuple(sorted(set(public_revealed_library_rules_names(card, record))))):
            continue
        for method,face in offered_cast_methods(host,card):
            if state.casts_once:
                if method is not None or not _qualifies_mana_value(host, card, record, face, state.reveal_count):
                    continue
            elif method not in (None,'fuse'):
                continue
            offered=build_cast_offer(host,actor,card,cast_method=method,face_name=face,
                resolving_payment='free',resolving_origin='library')
            if offered.offer is None:continue
            offer=offered.offer.to_dict(include_fingerprint=False)
            for price in offer['cost_options']:
                canonical={**price,'face':offer.get('face'),'cast_method':method}
                fingerprint=discover_options_fingerprint(({'logical_object_id':card.logical_object_id,'price':canonical},))
                prefix = "revealed-library-cast" if state.casts_once else "ripple"
                public={**canonical,'id':f'{prefix}:{card.ref}:{fingerprint}','card':card.ref,
                    'label':f"{card.printed_name} — {price.get('label',offer['label'])}"}
                pairs.append((public,canonical,card))
    return tuple(pairs)


def ripple_cast_options(host,*,item,actor,effect):
    if RIPPLE_STATE_FIELD not in effect:return ()
    raw={k:v for k,v in effect.items() if k!='_options_fingerprint'}
    state=RippleResolutionState.from_effect(raw,actor=actor)
    if item is None:raise GameRuleError('Ripple casting requires its resolving trigger')
    _live_item(host,actor,item.ref,state)
    if state.stage!='cast':return ()
    return tuple(public for public,_,_ in _option_pairs(host,actor,state))


def execute_ripple_cast(host,intent):
    item=_live_item(host,intent.actor,intent.stack_ref,intent.state)
    pairs=_option_pairs(host,intent.actor,intent.state)
    if discover_options_fingerprint(tuple(public for public,_,_ in pairs))!=intent.options_fingerprint:
        raise GameRuleError('Ripple casting choices changed before payment')
    response=thaw_value(intent.response);option_id=response.get('cost_option')
    matches=[(option,card) for public,option,card in pairs
        if option_id in (None,public['id']) and response.get('card',card.ref)==card.ref
        and response.get('face') in (None,option['face'])
        and response.get('cast_method') in (None,option['cast_method'])]
    if len(matches)!=1 or response.get('from','library')!='library':
        raise GameRuleError('Select one currently issued revealed-library spell and price')
    option,card=matches[0];original=card.logical_object_id
    response.update(card=card.ref,cost_option=option['id'],auto_pay=True)
    response['from']='library'
    for field in ('face','cast_method'):
        if option[field] is not None:response[field]=option[field]
    prefix = "revealed-library-cast" if intent.state.casts_once else "ripple"
    commit_resolving_cast(host,actor=intent.actor,response=response,origin='library',
        payment_id=f'{item.ref}:{prefix}:{original}',replacement_selections=intent.replacement_selections)
    current=host.state.cards[card.object_id]
    if (current.zone!='stack' or current.controller!=intent.actor or current.logical_object_id==original
            or not any(i.kind=='spell' and i.card_object_id==current.object_id for i in host.state.stack)):
        raise StateInvariantError('Ripple created no actual spell from its revealed library card')
    return current.ref
