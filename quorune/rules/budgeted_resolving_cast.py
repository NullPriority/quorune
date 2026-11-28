"""Resolve the offered hand/graveyard casts with as-cast type and aggregate price limits."""
from ..characteristic_evaluation import type_parts
from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError, StateInvariantError
from ..public_quantity_reductions import public_mana_value
from ..replacement.immutable import thaw_value
from ..resolving_cast_budget_model import CONTINUATION_FIELDS, program_dependencies, validate_effect
from .casting.characteristic_methods import characteristic_cast_view
from .casting.face_queries import offered_cast_methods, prospective_cast_card
from .casting.proposal import build_cast_offer
from .resolving_cast_payments import commit_resolving_cast
from .cast_grant_riders import CastGrantRiderBinding

_SCOPE = "budgeted_resolving_cast_values"


def _parent(host, item, actor):
    if (item is None or item.kind not in {"spell","spell_copy"} or item.controller != actor
            or actor not in host.active_seats or not item.context.get("currently_resolving")):
        raise GameRuleError("The bounded cast requires the current original spell frame")
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program) or not program_dependencies(program):
        raise GameRuleError("The current spell does not authorize a bounded resolving cast")
    return item


def _values(item):
    values = item.context.get(_SCOPE,())
    if (not isinstance(values,(tuple,list)) or len(values)>2
            or any(type(value) is not int or value<0 for value in values) or sum(values)>6):
        raise GameRuleError("Bounded resolving cast ledger is malformed")
    return tuple(values)


def _options_for_card(host, actor, card, limit):
    record = host.card_record(card)
    if record is None:
        return ()
    pairs = []
    for method, face in offered_cast_methods(host,card):
        if method is not None and (method != "fuse" or card.zone != "hand"):
            continue
        view, _ = characteristic_cast_view(host,card,record,method)
        effective = host._effective_card_data(prospective_cast_card(host,view,face))
        if type_parts(effective["type_line"])[0].isdisjoint({"instant","sorcery"}):
            continue
        amount = public_mana_value(effective)
        if type(amount) is not int or not 0<=amount<=limit:
            continue
        result = build_cast_offer(host,actor,card,cast_method=method,face_name=face,
                                  resolving_payment="free",resolving_origin=card.zone)
        if result.offer is None:
            continue
        offer = result.offer.to_dict(include_fingerprint=False)
        for price in offer["cost_options"]:
            canonical = {**price,"face":offer.get("face"),"cast_method":method}
            identity = discover_options_fingerprint(({"incarnation":card.logical_object_id,"price":canonical,
                                                        "mana_value":amount,"zone":card.zone},))
            public = {**canonical,"id":f"resolving-budget:{card.ref}:{identity}","card":card.ref,
                      "from":card.zone,"mana_value":amount,
                      "label":f"{card.printed_name} — {price.get('label',offer['label'])}"}
            pairs.append((public,canonical,card,amount))
    return tuple(pairs)


def _pairs(host, item, actor):
    _parent(host,item,actor)
    values = _values(item)
    if len(values) == 2:
        return ()
    player = host.state.players[actor]
    result = []
    for zone in ("hand","graveyard"):
        for oid in player.zones[zone]:
            card = host.state.cards[oid]
            if card.zone != zone or card.owner != actor or not card.is_card_object:
                raise GameRuleError("A bounded resolving cast has invalid zone membership")
            result.extend(_options_for_card(host,actor,card,6-sum(values)))
    return tuple(result)


def options(host, *, item, actor, effect):
    validate_effect({key:value for key,value in effect.items() if key not in CONTINUATION_FIELDS},actor=actor)
    return tuple(public for public,*_ in _pairs(host,item,actor))


def commit(host,intent):
    actor = intent.actor
    item = next((row for row in host.state.stack if row.ref == intent.stack_ref),None)
    pairs = _pairs(host,item,actor)
    if discover_options_fingerprint(tuple(public for public,*_ in pairs)) != intent.options_fingerprint:
        raise GameRuleError("The resolving cast options changed")
    response = thaw_value(intent.response)
    offer_id=response.get("cost_option")
    candidates = [(canonical,card,amount) for public,canonical,card,amount in pairs if offer_id == public["id"]
        and response.get("card",card.ref)==card.ref and response.get("from",card.zone)==card.zone
        and response.get("face") in (None,canonical["face"]) and response.get("cast_method") in (None,canonical["cast_method"])]
    if len(candidates)!=1 or "x" in response and (type(response["x"]) is not int or response["x"]!=0):
        raise GameRuleError("Select one issued face and cost with X equal to zero")
    canonical,card,amount = candidates[0]
    original=card.logical_object_id
    values=_values(item)
    zone=card.zone
    response.update(card=card.ref,cost_option=canonical["id"],auto_pay=True)
    response["from"]=zone
    for field in ("face","cast_method"):
        if canonical[field] is not None:
            response[field]=canonical[field]
    commit_resolving_cast(host,actor=actor,response=response,origin=zone,
        payment_id=f"{item.ref}:resolving-budget:{original}",replacement_selections=intent.replacement_selections)
    current=host.state.cards[card.object_id]
    if (current.zone!="stack" or current.logical_object_id==original or current.controller!=actor
            or not any(row.kind=="spell" and row.card_object_id==current.object_id for row in host.state.stack)):
        raise StateInvariantError("The selected bounded cast created no spell")
    from ..zone_object_state import mark_cast_grant_rider

    mark_cast_grant_rider(current,CastGrantRiderBinding("graveyard_exile",current.logical_object_id,
                                                       actor,item.ref,owner=current.owner))
    item.context[_SCOPE]=[*values,amount]
