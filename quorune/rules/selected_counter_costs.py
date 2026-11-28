from __future__ import annotations

"""One current controlled creature selected to pay a fixed counter cost."""

from collections.abc import Mapping

from .blight_model import BLIGHT_COUNTER, blight_creature_predicate

SELECTED_PLUS_COUNTER_COST_KIND = "remove_one_plus_one_counter"
SELECTED_MINUS_COUNTER_COST_KIND = "remove_one_minus_one_counter"
SELECTED_COUNTER_COST_CAPABILITY = "activation.selected_counter_removal.fixed"
SELECTED_PERMANENT_COUNTER_COST_KIND = "remove_one_counter_from_permanent"
SELECTED_PERMANENT_COUNTER_COST_CAPABILITY = "activation.selected_counter_removal.any_kind"
DISTRIBUTED_PLUS_COUNTER_COST_KIND = "remove_two_distributed_plus_one_counters"
DISTRIBUTED_PLUS_COUNTER_COST_CAPABILITY = "activation.selected_counter_removal.distributed_two"
BLIGHT_ACTIVATION_COST_KIND = "blight"
BLIGHT_ACTIVATION_COST_CAPABILITY = "activation.blight.fixed"
REMOVAL_COUNTER_NAMES = {
    SELECTED_PLUS_COUNTER_COST_KIND: "+1/+1",
    SELECTED_MINUS_COUNTER_COST_KIND: "-1/-1",
    DISTRIBUTED_PLUS_COUNTER_COST_KIND: "+1/+1",
}
SELECTED_COUNTER_COST_CAPABILITIES = {
    SELECTED_PLUS_COUNTER_COST_KIND: SELECTED_COUNTER_COST_CAPABILITY,
    SELECTED_PERMANENT_COUNTER_COST_KIND: SELECTED_PERMANENT_COUNTER_COST_CAPABILITY,
    SELECTED_MINUS_COUNTER_COST_KIND: SELECTED_COUNTER_COST_CAPABILITY,
    DISTRIBUTED_PLUS_COUNTER_COST_KIND: DISTRIBUTED_PLUS_COUNTER_COST_CAPABILITY,
    BLIGHT_ACTIVATION_COST_KIND: BLIGHT_ACTIVATION_COST_CAPABILITY,
}


def validate_selected_counter_choice(choice):
    card_type = "permanent" if choice.kind == SELECTED_PERMANENT_COUNTER_COST_KIND else "creature"
    if (choice.kind not in SELECTED_COUNTER_COST_CAPABILITIES
            or type(choice.count) is not int or choice.count != 1
            or choice.zone != "battlefield" or choice.card_type != card_type
            or choice.another is not False or choice.predicate is not None):
        raise ValueError(f"Selected counter costs require one controlled {card_type}")
    if choice.kind == BLIGHT_ACTIVATION_COST_KIND:
        if type(choice.counter_amount) is not int or choice.counter_amount <= 0:
            raise ValueError("A Blight cost requires a positive fixed counter amount")
    elif choice.counter_amount is not None:
        raise ValueError("Selected counter removal has its own fixed counter amount")


def blight_activation_choice(ability):
    return next((choice for choice in ability.choices if choice.kind == BLIGHT_ACTIVATION_COST_KIND), None)


def mana_first_counter_cost_choice(ability):
    """Use live mana procurement before paying a composed counter cost."""
    return next((choice for choice in ability.choices if choice.kind in (
        BLIGHT_ACTIVATION_COST_KIND, SELECTED_MINUS_COUNTER_COST_KIND,
    )), None)


def blight_activation_cost_is_closed(ability):
    return (
        ability.zones in (("battlefield",), ("graveyard",))
        and not (ability.mana_ability or ability.discard_source or ability.sacrifice_source
                 or ability.exile_source or ability.untap_source or ability.random_discard_payment)
        and ability.waterbend is None
        and ability.crew_threshold is None
        and ability.loyalty_delta is None
        and ability.source_counter_removal_cost is None
    )


def selected_counter_cost_candidates(host, actor, choice):
    from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
    from ..object_query import object_query_result, query_objects
    from ..semantic_runtime.counter_permissions import (
        allows_counter_placement_on_card, collect_counter_permissions,
    )

    validate_selected_counter_choice(choice)
    if choice.kind == SELECTED_PERMANENT_COUNTER_COST_KIND:
        return tuple(dict.fromkeys(card.ref for card, _payment in selected_permanent_counter_payments(host, actor)))
    blight = choice.kind == BLIGHT_ACTIVATION_COST_KIND
    permissions = collect_counter_permissions(host) if blight else None
    rows = []
    for object_id in host.state.players[actor].zones["battlefield"]:
        card = host.state.cards[object_id]
        data = host._effective_card_data(card)
        if blight and not allows_counter_placement_on_card(
            host, card, BLIGHT_COUNTER, choice.counter_amount, permissions=permissions, effective=data,
        ):
            continue
        rows.append(object_query_result(card, data,
            type_parts=host._type_parts(str(data.get("type_line") or "")),
            known_to_actor=True, attached_to_ref=None))
    query = blight_creature_predicate(actor) if blight else ObjectQuerySpec(
        zones=("battlefield",), controller=actor, types_all=("creature",),
        state_predicate=PermanentStatePredicateSpec(
            counter_name=REMOVAL_COUNTER_NAMES[choice.kind], minimum_counter_count=1,
        ),
    )
    eligible = tuple(query_objects(rows, query))
    if choice.kind == DISTRIBUTED_PLUS_COUNTER_COST_KIND and len(eligible) == 1:
        if not _can_pay_both_counters(host.state.cards[eligible[0].object_id]):
            return ()
    return tuple(row.ref for row in eligible)


def _can_pay_both_counters(card):
    return card.counters.get("+1/+1", 0) >= 2


def has_distributed_counter_cost(ability):
    return any(choice.kind == DISTRIBUTED_PLUS_COUNTER_COST_KIND for choice in ability.choices)


def blight_activation_cost_candidates(host, actor, source, ability, choice):
    return counter_cost_candidates_with_mana(host, actor, source, ability, choice)


def counter_cost_candidates_with_mana(host, actor, source, ability, choice):
    from ..cost_payments import sacrificing_mana_source_ids
    from ..mana_restrictions import ability_mana_spend_context
    from .activation.pricing import payable_activation_mana_options
    from .activation.x_costs import activation_requirements

    refs = selected_counter_cost_candidates(host, actor, choice)
    if not refs:
        return ()
    spend_context = ability_mana_spend_context(str(host._effective_card_data(source).get("type_line") or ""), source_zone=source.zone)
    excluded = {source.object_id} if ability.tap_source else set()
    sacrificing_sources = sacrificing_mana_source_ids(
        host, actor, refs, spend_context=spend_context, exclude_sources=excluded,
    )
    if not sacrificing_sources:
        return refs
    try:
        if ability.mana_cost_options:
            prices = tuple(
                (option.requirements, option.snow_payment)
                for option in payable_activation_mana_options(host, actor, source, ability)
            )
        else:
            requirements, _ = activation_requirements(host, actor, source, ability)
            prices = ((requirements, 0),)
    except ValueError:
        return ()
    return tuple(
        ref for ref in refs if ref not in sacrificing_sources or any(
            host._cost_is_affordable(actor, requirements, exclude_sources=excluded,
                spend_context=spend_context, snow_required=snow, preserve_source_id=sacrificing_sources[ref])
            for requirements, snow in prices
        )
    )


def _selected_counter_cost_ref(response):
    if ("cost_cards" in response and "cost_objects" in response
            and response["cost_cards"] != response["cost_objects"]):
        raise ValueError("Selected counter payment aliases disagree")
    refs = response.get("cost_cards", response.get("cost_objects"))
    if not isinstance(refs, (list, tuple)) or len(refs) != 1 or type(refs[0]) is not str or not refs[0]:
        raise ValueError("Choose exactly one creature to pay the counter cost")
    return refs[0]


def select_counter_cost_creature(host, actor, ability, response):
    if len(ability.choices) != 1:
        raise ValueError("Selected counter costs cannot combine selectable payment groups")
    choice = ability.choices[0]
    ref = _selected_counter_cost_ref(response)
    if ref not in selected_counter_cost_candidates(host, actor, choice):
        raise ValueError("The selected creature cannot pay this counter cost")
    return next(host.state.cards[object_id] for object_id in host.state.players[actor].zones["battlefield"]
                if host.state.cards[object_id].ref == ref)


def _mana_first_counter_error(exc, choice):
    from .activation.model import ActivationProposalError

    reason = "blight_cost_unpayable" if choice.kind == BLIGHT_ACTIVATION_COST_KIND else "selected_counter_cost_unpayable"
    return ActivationProposalError(str(exc), reason=reason)


def reserve_mana_first_counter_payer(host, proposal, ability, choice, response):
    try:
        payer = select_counter_cost_creature(host, proposal.seat, ability, response)
    except ValueError as exc:
        raise _mana_first_counter_error(exc, choice) from exc
    return payer.object_id, payer.ref, payer.logical_object_id


def pay_mana_first_counter_cost(host, proposal, source, ability, choice, payer_identity, response):
    from ..replacement.ordering import ReplacementChoiceRequired

    try:
        if choice.kind == BLIGHT_ACTIVATION_COST_KIND:
            return pay_blight_activation_cost(host, proposal, choice, payer_identity, response)
        return pay_selected_counter_cost(host, proposal.seat, source, ability, response)
    except ReplacementChoiceRequired:
        raise
    except ValueError as exc:
        raise _mana_first_counter_error(exc, choice) from exc


def _distributed_counter_selection(host, actor, ability, response):
    if len(ability.choices) != 1 or ability.choices[0].kind != DISTRIBUTED_PLUS_COUNTER_COST_KIND:
        raise ValueError("Distributed payment requires its one isolated counter cost")
    if ("cost_cards" in response and "cost_objects" in response
            and response["cost_cards"] != response["cost_objects"]):
        raise ValueError("Distributed counter payment aliases disagree")
    refs = response.get("cost_cards", response.get("cost_objects"))
    if (not isinstance(refs, (list, tuple)) or len(refs) not in {1, 2}
            or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs)):
        raise ValueError("Choose one creature with two +1/+1 counters or two creatures with one each")
    legal = selected_counter_cost_candidates(host, actor, ability.choices[0])
    if any(ref not in legal for ref in refs):
        raise ValueError("Only creatures you control with available +1/+1 counters can pay")
    by_ref = {host.state.cards[object_id].ref: host.state.cards[object_id]
              for object_id in host.state.players[actor].zones["battlefield"]}
    cards = tuple(by_ref[ref] for ref in refs)
    if len(cards) == 1 and not _can_pay_both_counters(cards[0]):
        raise ValueError("The selected creature needs two +1/+1 counters")
    return cards, 2 if len(cards) == 1 else 1


def selected_permanent_counter_payments(host, actor):
    """Offer a public choice identifying the counter and its current controlled permanent."""
    from .activation.source_counter_choices import source_counter_payment_choices
    from .activation_counter_cost import SourceCounterRemovalCost

    cost = SourceCounterRemovalCost(None, 1, 2)
    for object_id in host.state.players[actor].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller == actor:
            for payment in source_counter_payment_choices(card, cost):
                yield card, payment


def _selected_permanent_counter_payment(host, actor, response):
    from .activation.source_counter_choices import SOURCE_COUNTER_PAYMENT_FIELD

    value = response.get(SOURCE_COUNTER_PAYMENT_FIELD)
    if type(value) is not str or "cost_cards" in response or "cost_objects" in response:
        raise ValueError("Choose one counter on a permanent you control")
    for card, payment in selected_permanent_counter_payments(host, actor):
        if payment.choice_id == value:
            (name, amount), = payment.removals
            return card, name, amount
    raise ValueError("The selected permanent counter payment is unavailable or stale")


def pay_selected_counter_cost(host, actor, source, ability, response):
    from ..counter_removal import CounterRemoval, commit_counter_removals, plan_counter_removals

    counter_name = REMOVAL_COUNTER_NAMES.get(ability.choices[0].kind, "+1/+1")
    if ability.choices[0].kind == SELECTED_PERMANENT_COUNTER_COST_KIND:
        card, counter_name, amount = _selected_permanent_counter_payment(host, actor, response)
        cards = (card,)
    elif has_distributed_counter_cost(ability):
        cards, amount = _distributed_counter_selection(host, actor, ability, response)
    else:
        cards, amount = (select_counter_cost_creature(host, actor, ability, response),), 1
    plan = plan_counter_removals(host, tuple(CounterRemoval(object_id=card.object_id,
        counter_name=counter_name, amount=amount, expected_zone="battlefield",
        expected_logical_object_id=card.logical_object_id) for card in cards))
    commit_counter_removals(host, plan)
    return [card.object_id for card in cards]


def _blight_payment_event_prefix(payment_id, source_ref, ability_id):
    return f"counter.cost:{payment_id}:{source_ref}:{ability_id}:blight:"


def _blight_payment_event_id(payment_id, source_ref, ability_id, ref, object_id, logical_id, amount):
    prefix = _blight_payment_event_prefix(payment_id, source_ref, ability_id)
    return f"{prefix}{ref}:{object_id}:{logical_id}:{amount}"


def pay_blight_activation_cost(host, proposal, choice, payer_identity, response):
    from ..counter_placement import (
        CounterPlacementRequest, commit_prepared_counter_placements, prepare_counter_placements,
    )
    from ..mana_mode_effects import mana_result_event_belongs_to_payment

    object_id, ref, logical_id = payer_identity
    payer = host.state.cards.get(object_id)
    if (payer is None or payer.ref != ref or payer.logical_object_id != logical_id
            or ref not in selected_counter_cost_candidates(host, proposal.seat, choice)):
        raise ValueError("The selected creature can no longer pay Blight")
    payment_id = response.get("_mana_payment_id") or proposal.fingerprint
    event_id = _blight_payment_event_id(
        payment_id, proposal.source_ref, proposal.ability_id,
        payer.ref, payer.object_id, payer.logical_object_id, choice.counter_amount,
    )
    journal = response.get("_mana_replacement_selections") or {}
    if not isinstance(journal, Mapping) or any(
        key != event_id and not mana_result_event_belongs_to_payment(key, payment_id)
        for key in journal
    ):
        raise ValueError("Blight activation replacement journal is malformed")
    selections = journal.get(event_id, ())
    if not isinstance(selections, (list, tuple)):
        raise ValueError("Blight activation replacement selections are malformed")
    prepared = prepare_counter_placements(
        host,
        (CounterPlacementRequest(
            subject_kind="permanent", subject_id=payer.object_id, counter_name=BLIGHT_COUNTER,
            amount=choice.counter_amount, placing_player=proposal.seat,
            source_ref=proposal.source_ref, effect_generated=False,
        ),),
        selections=tuple(selections), event_ids=(event_id,), for_cost=True,
    )
    commit_prepared_counter_placements(host, prepared, reason="Blight activation cost")
    return [payer.object_id]


def selected_counter_cost_choice_schema(ability, hint, *, host=None, actor=None):
    choices = tuple(choice for choice in ability.choices if choice.kind in SELECTED_COUNTER_COST_CAPABILITIES)
    if not choices:
        return None
    if len(choices) != 1 or len(ability.choices) != 1:
        raise ValueError("Selected counter costs require one isolated public selection")
    choice = choices[0]
    if choice.kind == SELECTED_PERMANENT_COUNTER_COST_KIND:
        from .activation.source_counter_choices import SOURCE_COUNTER_PAYMENT_FIELD

        if host is None or actor is None:
            raise ValueError("A selected permanent counter cost requires its current payer")
        payments = tuple(selected_permanent_counter_payments(host, actor))
        return {SOURCE_COUNTER_PAYMENT_FIELD: {
            "label": "Counter to remove",
            "legal_values": [payment.choice_id for _card, payment in payments],
            "option_labels": {payment.choice_id: f"{payment.label} from {host.display_name(card.object_id)}"
                              for card, payment in payments},
        }}
    if choice.kind == DISTRIBUTED_PLUS_COUNTER_COST_KIND:
        if host is None or actor is None:
            raise ValueError("Distributed counter choice requires its current payer")
        refs = tuple(hint["choose_cost"][0]["legal_refs"])
        single = [host.state.cards[object_id].ref for object_id in host.state.players[actor].zones["battlefield"]
                  if host.state.cards[object_id].ref in refs and _can_pay_both_counters(host.state.cards[object_id])]
        counts = [count for count, available in ((1, bool(single)), (2, len(refs) >= 2)) if available]
        if not counts:
            return None
        return {"cost_cards": {"type": "array",
            "label": "Remove two +1/+1 counters from one creature or one each from two creatures",
            "legal_refs": list(refs), "minimum": min(counts), "maximum": max(counts),
            "allowed_counts": counts, "single_creature_refs": single}}
    label = (
        f"Creature to blight {choice.counter_amount}" if choice.kind == BLIGHT_ACTIVATION_COST_KIND
        else f"Creature to remove a {REMOVAL_COUNTER_NAMES[choice.kind]} counter from"
    )
    return {"cost_cards": {"type": "array", "label": label,
        "legal_refs": list(hint["choose_cost"][0]["legal_refs"]),
        "minimum": 1, "maximum": 1}}


def _valid_compact_selected_counter_cost(choice):
    kind = choice.get("k")
    if type(kind) is not str or kind not in SELECTED_COUNTER_COST_CAPABILITIES:
        return False
    fields = {"k", "n", "z", "t"}
    if kind == BLIGHT_ACTIVATION_COST_KIND:
        fields.add("amount")
        if type(choice.get("amount")) is not int or choice["amount"] <= 0:
            return False
    return (
        set(choice) == fields and type(choice["n"]) is int and choice["n"] == 1
        and choice["z"] == "battlefield"
        and choice["t"] == ("permanent" if kind == SELECTED_PERMANENT_COUNTER_COST_KIND else "creature")
    )


def selected_counter_runtime_dependencies(program):
    schema = getattr(program, "cost_schema", None)
    declared = getattr(program, "capability_dependencies", ())
    declared_distributed = DISTRIBUTED_PLUS_COUNTER_COST_CAPABILITY in declared
    declared_permanent = SELECTED_PERMANENT_COUNTER_COST_CAPABILITY in declared
    if not isinstance(schema, Mapping):
        if declared_permanent:
            raise ValueError("A selected permanent counter payment requires its cost schema")
        if declared_distributed:
            raise ValueError("A distributed counter payment requires its cost schema")
        return ()
    choices = schema.get("choices") or ()
    selected = [
        choice for choice in choices
        if isinstance(choice, Mapping) and type(choice.get("k")) is str
        and choice["k"] in SELECTED_COUNTER_COST_CAPABILITIES
    ]
    if not selected:
        if declared_permanent:
            raise ValueError("A selected permanent counter payment requires its typed cost choice")
        if declared_distributed:
            raise ValueError("A distributed counter payment requires its typed cost choice")
        return ()
    if declared_permanent and selected[0].get("k") != SELECTED_PERMANENT_COUNTER_COST_KIND:
        raise ValueError("A selected permanent counter payment cannot silently become a different cost")
    if (selected[0].get("k") == SELECTED_PERMANENT_COUNTER_COST_KIND
            and (getattr(program, "active_zone", None) != "battlefield"
                 or schema.get("tap_source") is not True)):
        raise ValueError("A selected permanent counter payment requires its battlefield tap activation")
    if declared_distributed and selected[0].get("k") != DISTRIBUTED_PLUS_COUNTER_COST_KIND:
        raise ValueError("A distributed counter payment cannot silently become a different cost")
    if selected[0].get("k") == DISTRIBUTED_PLUS_COUNTER_COST_KIND and getattr(program, "active_zone", None) != "battlefield":
        raise ValueError("A distributed counter payment requires a battlefield activation")
    if (len(selected) != 1 or len(choices) != 1
            or not _valid_compact_selected_counter_cost(selected[0])
            or getattr(program, "event", None) != "activate"):
        raise ValueError("Selected counter payment requires its complete activation cost owner")
    if selected[0]["k"] == SELECTED_MINUS_COUNTER_COST_KIND:
        _validate_hybrid_counter_price(program, schema)
    return (SELECTED_COUNTER_COST_CAPABILITIES[selected[0]["k"]],)


def _validate_hybrid_counter_price(program, schema):
    from ..activation_mana_cost import ActivationManaCostOption, fixed_complex_activation_mana_options

    raw_options = schema.get("mana_cost_options")
    if getattr(program, "active_zone", None) != "battlefield" or not isinstance(raw_options, (tuple, list)):
        raise ValueError("Selected -1/-1 counter payment requires the complete battlefield hybrid price")
    options = tuple(ActivationManaCostOption.from_dict(option) for option in raw_options)
    if options != fixed_complex_activation_mana_options({}, ("B/G",)):
        raise ValueError("Selected -1/-1 counter payment requires both black and green mana options")


def _offered_blight_cost(response):
    summary = response.get("cost_summary")
    choices = summary.get("choose_cost") if isinstance(summary, Mapping) else None
    if not isinstance(choices, (list, tuple)) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        return None
    choice = dict(choices[0])
    legal = choice.pop("legal_refs", None)
    if choice.get("k") != BLIGHT_ACTIVATION_COST_KIND or not _valid_compact_selected_counter_cost(choice):
        return None
    if (not isinstance(legal, (list, tuple)) or not legal
            or any(type(value) is not str or not value for value in legal) or len(legal) != len(set(legal))):
        return None
    try:
        ref = _selected_counter_cost_ref(response)
    except ValueError:
        return None
    return (choice["amount"], ref) if ref in legal else None


def blight_prior_mana_replacement_event_ids(response):
    from ..mana_mode_effects import mana_result_event_belongs_to_payment

    journal = response.get("_mana_replacement_selections")
    if not isinstance(journal, Mapping) or _offered_blight_cost(response) is None:
        return set()
    payment_id = response.get("_mana_payment_id")
    return {key for key in journal if mana_result_event_belongs_to_payment(key, payment_id)}


def is_blight_activation_cost_event(event, response):
    prefix = _blight_payment_event_prefix(
        response.get("_mana_payment_id"), response.get("source"), response.get("ability"),
    )
    return event.event_id.startswith(prefix) or _offered_blight_cost(response) is not None


def blight_activation_cost_event_is_valid(batch, effects, response, *, seat):
    from ..replacement.ordering import counter_cost_replacement_progress_matches

    event, = batch.events
    offered = _offered_blight_cost(response)
    if offered is None:
        return False
    amount, ref = offered
    payment_id, source_ref, ability_id = (response.get(key) for key in ("_mana_payment_id", "source", "ability"))
    if not all(type(value) is str and value for value in (payment_id, source_ref, ability_id)):
        return False
    affected = event.affected_object
    payload = event.payload
    logical_id = payload.get("target_logical_object_id")
    target_types = payload.get("target_types")
    if not (
        not event.children and event.entry_scope is None
        and affected is not None and affected.controller == seat
        and payload.get("target_controller") == seat and payload.get("target_zone") == "battlefield"
        and isinstance(target_types, (list, tuple)) and "creature" in target_types
        and payload.get("prospective_subject") is not True
        and type(logical_id) is str and logical_id
        and payload.get("counter_name") == BLIGHT_COUNTER
        and type(payload.get("requested_amount")) is int
        and amount == payload["requested_amount"]
        and payload.get("source") == source_ref
        and event.event_id == _blight_payment_event_id(
            payment_id, source_ref, ability_id, ref, affected.object_id, logical_id, amount,
        )
    ):
        return False

    journal = response.get("_mana_replacement_selections") or {}
    if not isinstance(journal, Mapping):
        return False
    prior_choices = journal.get(event.event_id, ())
    if not isinstance(prior_choices, (list, tuple)) or any(
        type(value) is not str or not value for value in prior_choices
    ):
        return False
    return counter_cost_replacement_progress_matches(batch, effects, prior_choices, amount)
