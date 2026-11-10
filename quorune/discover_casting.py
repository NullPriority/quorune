from __future__ import annotations

"""Current resolving-cast permission over canonical exile casting and zones."""

from .discover_model import (ExileCastProgress, discover_spell_qualifies, discover_options_fingerprint,
    QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_STEP_OPERATION, validate_qualified_scan_effect)
from .errors import GameRuleError,StateInvariantError
from .fixed_top_exile import (CHOSEN_IMPULSE_SCOPE, FIXED_TOP_EXILE_OPERATION, FIXED_TOP_EXILE_OFFER_OPERATION,
    RESOLVING_TOP_PLAY_SCOPE, fixed_top_exile_result_rows, validate_fixed_top_exile)
from .rules.land_play_permissions import resolving_exile_land_play_options
from .replacement.immutable import thaw_value
from .rules.casting.face_queries import offered_cast_methods, prospective_cast_card
from .rules.casting.proposal import _static_cast_rule_state, build_cast_offer
from .rules.resolving_cast_payments import commit_resolving_exile_cast


def resolving_exile_library_refs(host, *, actor, effect):
    """Expose the next supplied identity solely to the server's scan snapshot."""
    operation = effect.get('op')
    if operation == FIXED_TOP_EXILE_OPERATION:
        scope = validate_fixed_top_exile(effect, resolved=True)
        if effect["player"] != actor:
            raise StateInvariantError("The fixed top exile controller changed")
        owners = host.active_seats if scope == "each_library" else (
            (effect["supplier"],) if scope == "cast_event_library" else ())
        if any(owner not in host.state.players for owner in owners):
            raise StateInvariantError("Fixed top exile names an unknown supplying player")
        return {owner: [host.state.cards[key].ref for key in host.state.players[owner].zones["library"][-1:]]
                for owner in owners if owner != actor and owner in host.active_seats}
    return qualified_scan_library_refs(host, actor=actor, effect=effect)


def qualified_scan_library_refs(host, *, actor, effect):
    """Expose the one current supplier top to the qualified-scan snapshot."""
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
    from .rules.graveyard_copy_cast_model import GRAVEYARD_COPY_PARENT_FIELD

    cards=[]
    owner = progress.supplier or actor
    for identity in progress.exiled if include_candidate else progress.cleanup_cards:
        card=host.state.cards.get(identity.object_id)
        if (card is None or card.ref!=identity.ref or card.logical_object_id!=identity.logical_object_id
                or card.owner!=owner or card.zone!='exile'):
            raise GameRuleError('A Discover exile incarnation changed')
        if progress.copy_parent_ref is None:
            if not card.is_card_object:
                raise GameRuleError('A Discover exile incarnation must remain a physical card')
        elif (owner != actor or not card.is_card_copy
                or card.annotations.get(GRAVEYARD_COPY_PARENT_FIELD) != progress.copy_parent_ref):
            raise GameRuleError('The resolving copy lost its parent or exact incarnation')
        cards.append(card)
    return tuple(cards)


def _discover_cast_option_pairs(host,*,item,actor,effect):
    if item is None:return ()
    require_discover_resolution(host,actor=actor,stack_ref=item.ref)
    progress=ExileCastProgress.from_dict(effect['progress'])
    if progress.copy_parent_ref is not None and progress.copy_parent_ref != item.ref:
        raise GameRuleError("An exiled copy is authorized only during its original resolution")
    require_discover_exile(host,progress,actor=actor)
    if progress.candidate is None:return ()
    card=host.state.cards[progress.candidate.object_id];record=host.card_record(card)
    if progress.fixed_top_payment is not None:
        return _fixed_top_cast_option_pairs(host, actor=actor, card=card, progress=progress)
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


def fixed_top_exile_cast_options(host, *, item, actor, effect):
    if item is None:
        return ()
    require_discover_resolution(host, actor=actor, stack_ref=item.ref)
    fields = {"op", "instruction", "results"}
    if (set(effect) not in (fields, fields | {"_stack_ref", "_options_fingerprint"})
            or effect.get("_stack_ref", item.ref) != item.ref
            or effect.get("op") != FIXED_TOP_EXILE_OFFER_OPERATION):
        raise GameRuleError("Fixed top exile casting has a malformed resolving offer")
    offer = {key: effect[key] for key in fields}
    rows = fixed_top_exile_result_rows(offer)
    instruction = offer["instruction"]
    if instruction["player"] != actor:
        raise GameRuleError("Fixed top exile casting controller changed")
    scope = instruction["scope"]
    if scope == CHOSEN_IMPULSE_SCOPE:
        return ()
    payment = "paid" if scope in {"controller_library", RESOLVING_TOP_PLAY_SCOPE} else "free"
    options = []
    for owner, identity in rows:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.zone != "exile" or card.logical_object_id != identity.logical_object_id
                or card.ref != identity.ref or card.owner != owner or not card.is_card_object or card.face_down):
            continue
        if scope == "cast_event_library":
            record = host.card_record(card)
            if record is None:
                continue
            front_type = record.faces[0].get("type_line", "") if record.faces else record.type_line
            if "land" in host._type_parts(front_type)[0]:
                continue
        progress = ExileCastProgress(None, (identity,), identity.ref, owner, payment)
        options.extend(public for public, _ in _fixed_top_cast_option_pairs(host, actor=actor, card=card, progress=progress))
        if scope == RESOLVING_TOP_PLAY_SCOPE:
            options.extend(resolving_exile_land_play_options(host, actor, card))
    return tuple(options)


def _fixed_top_cast_option_pairs(host, *, actor, card, progress):
    if card.face_down:
        return ()
    prefix = (f"graveyard-copy:{card.ref}:" if progress.copy_parent_ref is not None
              else f"fixed-top:{progress.fixed_top_payment}:{card.ref}:")
    pairs = []
    for method, face in offered_cast_methods(host, card):
        if progress.copy_parent_ref is not None and method is not None:
            continue
        result = build_cast_offer(host, actor, card, cast_method=method, face_name=face,
                                  resolving_payment=progress.fixed_top_payment)
        if result.offer is None:
            continue
        offer = result.offer.to_dict(include_fingerprint=False)
        required = {}
        if progress.copy_parent_ref is not None:
            from .rules.resolving_cast_payments import resolving_price_requires_cast
            required = {option.option_id: resolving_price_requires_cast(option.to_dict()) for option in result.cost_options}
        for price in offer["cost_options"]:
            option = {**price, "face": offer.get("face"), "cast_method": method}
            label = price.get("label", offer["label"])
            if method == "prototype":
                label = f"{label} (prototype)"
            public = {**option, "id": prefix + discover_options_fingerprint((option,)), "card": card.ref,
                      "label": f"{card.printed_name} — {label}"}
            if progress.copy_parent_ref is not None:
                public["required_cast"] = required[price["id"]]
            pairs.append((public, option))
    return tuple(pairs)


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
    if intent.progress.fixed_top_payment is not None:
        method = option["cast_method"]
        if response.get("cast_method") not in (None, method):
            raise GameRuleError("The casting method changed from the selected option")
        if method is not None:
            response["cast_method"] = method
        producer = "graveyard-copy" if intent.progress.copy_parent_ref is not None else "fixed-top-exile"
    elif intent.progress.supplier is not None:
        producer = 'exile-until-instant-or-sorcery'
    else:
        producer = 'exile-until-nonland' if intent.progress.amount is None else 'discover'
    commit_resolving_exile_cast(host,actor=intent.actor,response=response,
        payment_id=f'{intent.stack_ref}:{producer}:{intent.progress.candidate.logical_object_id}',
        replacement_selections=intent.replacement_selections,
        force_without_mana_cost=intent.progress.fixed_top_payment != "paid")
    spell = next((stack for stack in host.state.stack if stack.ref not in before
                  and stack.kind == "spell" and stack.card_object_id == card.object_id), None)
    if spell is None:
        raise StateInvariantError('The Discover choice created no spell')
    if intent.progress.copy_parent_ref is not None and spell.default_destination == "battlefield":
        from .zone_object_state import mark_resolving_graveyard_copy
        mark_resolving_graveyard_copy(host.state.cards[card.object_id], item.ref, zone="stack")
