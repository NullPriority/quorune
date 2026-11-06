from __future__ import annotations

"""One current controlled creature selected to pay a fixed counter cost."""

from collections.abc import Mapping

from .blight_model import BLIGHT_COUNTER, blight_creature_predicate

SELECTED_PLUS_COUNTER_COST_KIND = "remove_one_plus_one_counter"
SELECTED_COUNTER_COST_CAPABILITY = "activation.selected_counter_removal.fixed"
BLIGHT_ACTIVATION_COST_KIND = "blight"
BLIGHT_ACTIVATION_COST_CAPABILITY = "activation.blight.fixed"
SELECTED_COUNTER_COST_CAPABILITIES = {
    SELECTED_PLUS_COUNTER_COST_KIND: SELECTED_COUNTER_COST_CAPABILITY,
    BLIGHT_ACTIVATION_COST_KIND: BLIGHT_ACTIVATION_COST_CAPABILITY,
}


def validate_selected_counter_choice(choice):
    if (choice.kind not in SELECTED_COUNTER_COST_CAPABILITIES
            or type(choice.count) is not int or choice.count != 1
            or choice.zone != "battlefield" or choice.card_type != "creature"
            or choice.another is not False or choice.predicate is not None):
        raise ValueError("Selected counter costs require one controlled creature")
    if choice.kind == BLIGHT_ACTIVATION_COST_KIND:
        if type(choice.counter_amount) is not int or choice.counter_amount <= 0:
            raise ValueError("A Blight cost requires a positive fixed counter amount")
    elif choice.counter_amount is not None:
        raise ValueError("Selected counter removal requires exactly one +1/+1 counter")


def blight_activation_choice(ability):
    return next((choice for choice in ability.choices if choice.kind == BLIGHT_ACTIVATION_COST_KIND), None)


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
        state_predicate=PermanentStatePredicateSpec(counter_name="+1/+1", minimum_counter_count=1),
    )
    return tuple(row.ref for row in query_objects(rows, query))


def blight_activation_cost_candidates(host, actor, source, ability, choice):
    from ..mana import mana_modes_preserving_source
    from ..mana_restrictions import ability_mana_spend_context
    from .activation.pricing import payable_activation_mana_options
    from .activation.x_costs import activation_requirements

    refs = selected_counter_cost_candidates(host, actor, choice)
    if not refs:
        return ()
    spend_context = ability_mana_spend_context(str(host._effective_card_data(source).get("type_line") or ""))
    excluded = {source.object_id} if ability.tap_source else set()
    sacrificing_sources = {
        candidate.ref: candidate.object_id
        for candidate in host.available_mana_sources(actor, spend_context=spend_context)
        if candidate.ref in refs and candidate.object_id not in excluded
        and len(mana_modes_preserving_source(candidate.modes)) != len(candidate.modes)
    }
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


def pay_selected_counter_cost(host, actor, source, ability, response):
    from ..counter_removal import CounterRemoval, commit_counter_removals, plan_counter_removals

    card = select_counter_cost_creature(host, actor, ability, response)
    plan = plan_counter_removals(host, (CounterRemoval(object_id=card.object_id,
        counter_name="+1/+1", amount=1, expected_zone="battlefield",
        expected_logical_object_id=card.logical_object_id),))
    commit_counter_removals(host, plan)
    return [card.object_id]


def _blight_payment_event_prefix(payment_id, source_ref, ability_id):
    return f"counter.cost:{payment_id}:{source_ref}:{ability_id}:blight:"


def _blight_payment_event_id(payment_id, source_ref, ability_id, ref, object_id, logical_id, amount):
    prefix = _blight_payment_event_prefix(payment_id, source_ref, ability_id)
    return f"{prefix}{ref}:{object_id}:{logical_id}:{amount}"


def pay_blight_activation_cost(host, proposal, choice, payer_identity, response):
    from ..counter_placement import (
        CounterPlacementRequest, commit_prepared_counter_placements, prepare_counter_placements,
    )
    from ..mana_mode_effects import mana_damage_event_belongs_to_payment

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
        key != event_id and not mana_damage_event_belongs_to_payment(key, payment_id)
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


def selected_counter_cost_choice_schema(ability, hint):
    choices = tuple(choice for choice in ability.choices if choice.kind in SELECTED_COUNTER_COST_CAPABILITIES)
    if not choices:
        return None
    if len(choices) != 1 or len(ability.choices) != 1:
        raise ValueError("Selected counter costs require one isolated public selection")
    choice = choices[0]
    label = (
        f"Creature to blight {choice.counter_amount}" if choice.kind == BLIGHT_ACTIVATION_COST_KIND
        else "Creature to remove a +1/+1 counter from"
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
        and choice["z"] == "battlefield" and choice["t"] == "creature"
    )


def selected_counter_runtime_dependencies(program):
    schema = getattr(program, "cost_schema", None)
    if not isinstance(schema, Mapping):
        return ()
    choices = schema.get("choices") or ()
    selected = [
        choice for choice in choices
        if isinstance(choice, Mapping) and type(choice.get("k")) is str
        and choice["k"] in SELECTED_COUNTER_COST_CAPABILITIES
    ]
    if not selected:
        return ()
    if (len(selected) != 1 or len(choices) != 1
            or not _valid_compact_selected_counter_cost(selected[0])
            or getattr(program, "event", None) != "activate"):
        raise ValueError("Selected counter payment requires its complete activation cost owner")
    return (SELECTED_COUNTER_COST_CAPABILITIES[selected[0]["k"]],)


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
    from ..mana_mode_effects import mana_damage_event_belongs_to_payment

    journal = response.get("_mana_replacement_selections")
    if not isinstance(journal, Mapping) or _offered_blight_cost(response) is None:
        return set()
    payment_id = response.get("_mana_payment_id")
    return {key for key in journal if mana_damage_event_belongs_to_payment(key, payment_id)}


def is_blight_activation_cost_event(event, response):
    prefix = _blight_payment_event_prefix(
        response.get("_mana_payment_id"), response.get("source"), response.get("ability"),
    )
    return event.event_id.startswith(prefix) or _offered_blight_cost(response) is not None


def blight_activation_cost_event_is_valid(batch, effects, response, *, seat):
    from dataclasses import replace
    from ..replacement.model import COUNTER_REPLACEMENT_GENERATED_FIELD
    from ..replacement.ordering import advance_replacement_batch

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
    # The suspended event may already include earlier counter replacements.
    # Rebuild the requested event and check progress through the existing owner.
    original_payload = {**payload, "amount": amount}
    original_payload.pop("replacement_choosers", None)
    if COUNTER_REPLACEMENT_GENERATED_FIELD in original_payload:
        original_payload[COUNTER_REPLACEMENT_GENERATED_FIELD] = False
    original = replace(event, payload=original_payload, applied_effects=())
    progress = advance_replacement_batch(
        replace(batch, events=(original,), journal=()), effects, selections=tuple(prior_choices),
    )
    return progress.pending is not None and progress.batch == batch
