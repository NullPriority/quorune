from __future__ import annotations

"""Canonical exact source-counter payment for activated abilities."""

from typing import Any, Mapping, Protocol
from dataclasses import replace

from ...abilities import ActivatedAbility, choose_ability
from ...activated_ability_descriptor import SOURCE_COUNTER_PLACEMENT_COST, SOURCE_COUNTER_PLACEMENT_OFFER_FIELD
from ...counter_removal import (
    commit_counter_removals,
    CounterRemoval,
    CounterRemovalError,
    plan_counter_removals,
)


SOURCE_COUNTER_PLACEMENT_ORIGIN = "_source_counter_placement_origin"


class SourceCounterActivationCostError(ValueError):
    """A typed source-counter activation cost cannot be paid exactly."""


class SourceCounterActivationCostHost(Protocol):
    state: Any


def plan_source_counter_removal_cost(
    host: SourceCounterActivationCostHost,
    source: Any,
    ability: ActivatedAbility,
    *, response=None,
):
    """Plan the same identity-pinned exact removal for offers and payment."""

    return plan_source_counter_removal_payment(host,source,ability.source_counter_removal_cost,response=response)


def plan_source_counter_removal_payment(host,source,cost,*,response=None):
    """One identity-pinned cost plan shared by explicit and automatic activation."""
    if cost is None:
        return None
    if cost.schema_version == 2:
        from .source_counter_choices import selected_source_counter_removals
        try:
            selected = selected_source_counter_removals(source, cost, response)
            return plan_counter_removals(host, tuple(CounterRemoval(object_id=source.object_id,
                counter_name=name, amount=amount, expected_zone='battlefield',
                expected_logical_object_id=source.logical_object_id) for name, amount in selected))
        except ValueError as exc:
            raise SourceCounterActivationCostError(str(exc)) from exc
    try:
        return plan_counter_removals(
            host,
            (
                CounterRemoval(
                    object_id=source.object_id,
                    counter_name=cost.counter_name,
                    amount=cost.amount,
                    expected_zone="battlefield",
                    expected_logical_object_id=source.logical_object_id,
                ),
            ),
        )
    except CounterRemovalError as exc:
        raise SourceCounterActivationCostError(
            "The source no longer has enough counters"
        ) from exc


def preview_source_counter_removal_cost(host, source, ability):
    """Return detached post-payment counter facts without committing a cost."""
    plan = plan_source_counter_removal_cost(host, source, ability)
    counters = dict(source.counters)
    if plan is not None:
        for transition in plan.counter_plan.transitions:
            counters[transition.counter_name] = transition.after
    return replace(source, counters=counters)


def commit_source_counter_removal_cost(host, source, ability, *, response=None) -> None:
    """Pay the exact plan used by the read-only source-counter preview."""
    plan = plan_source_counter_removal_cost(host, source, ability, response=response)
    if plan is not None:
        try:
            commit_counter_removals(host, plan)
        except CounterRemovalError as exc:
            raise SourceCounterActivationCostError("The source no longer has enough counters") from exc


def source_counter_placement_is_payable(host, actor, source):
    from ...semantic_runtime.counter_permissions import allows_counter_placement_on_card

    current = host.state.cards.get(source.object_id)
    return (
        current is not None and current.controller == actor and current.zone == "battlefield"
        and current.ref == source.ref and current.logical_object_id == source.logical_object_id
        and not current.phased_out
        and allows_counter_placement_on_card(host, current, SOURCE_COUNTER_PLACEMENT_COST.counter_name, 1)
    )


def _source_placement_event_id(payment_id, source_ref, ability_id, logical_id):
    return f"counter.cost:{payment_id}:{source_ref}:{ability_id}:source-placement:{logical_id}"


def _source_placement_origin(source):
    return {"object_id": source.object_id, "ref": source.ref, "logical_id": source.logical_object_id}


def freeze_source_counter_placement_response(host, actor, response):
    summary = response.get("cost_summary")
    if not isinstance(summary, Mapping) or SOURCE_COUNTER_PLACEMENT_OFFER_FIELD not in summary:
        return response
    from ...errors import GameRuleError
    from .model import ActivationProposalRequest

    request = ActivationProposalRequest.from_submission(actor, response)
    source = host._resolve_object(actor, request.source_ref, zones={"battlefield"}, controlled_only=True)
    ability = choose_ability(host._activated_abilities(source), request.ability_selector)
    cost = ability.source_counter_placement_cost
    if cost is None or summary[SOURCE_COUNTER_PLACEMENT_OFFER_FIELD] != cost.to_dict():
        raise GameRuleError("The original source counter price changed")
    origin = _source_placement_origin(source)
    if response.get(SOURCE_COUNTER_PLACEMENT_ORIGIN, origin) != origin:
        raise GameRuleError("The original source incarnation changed")
    return {**response, SOURCE_COUNTER_PLACEMENT_ORIGIN: origin}


def pay_source_counter_placement(host, proposal, source, response):
    from ...counter_placement import (
        CounterPlacementError, CounterPlacementRequest, commit_prepared_counter_placements, prepare_counter_placements,
    )
    from ...errors import GameRuleError

    if response.get("cost_cards") or response.get("cost_objects"):
        raise GameRuleError("The source counter price does not select a payment object")
    if not source_counter_placement_is_payable(host, proposal.seat, source):
        raise GameRuleError("The original source cannot receive the activation counter")
    origin = _source_placement_origin(source)
    if response.get(SOURCE_COUNTER_PLACEMENT_ORIGIN, origin) != origin:
        raise GameRuleError("The original source incarnation changed")
    payment_id = response.get("_mana_payment_id") or proposal.fingerprint
    event_id = _source_placement_event_id(payment_id, proposal.source_ref, proposal.ability_id, source.logical_object_id)
    journal = response.get("_mana_replacement_selections", {})
    if not isinstance(journal, Mapping) or set(journal) - {event_id}:
        raise GameRuleError("The source counter payment replacement journal is malformed")
    selections = journal.get(event_id, ())
    if not isinstance(selections, (list, tuple)) or any(type(choice) is not str or not choice for choice in selections):
        raise GameRuleError("The source counter payment replacement selections are malformed")
    request = CounterPlacementRequest(
        subject_kind="permanent", subject_id=source.object_id, counter_name=SOURCE_COUNTER_PLACEMENT_COST.counter_name,
        amount=1, placing_player=proposal.seat, source_ref=source.ref, effect_generated=False,
    )
    try:
        prepared = prepare_counter_placements(host, (request,), selections=tuple(selections),
                                              event_ids=(event_id,), for_cost=True)
        commit_prepared_counter_placements(host, prepared, reason="source counter activation cost")
    except CounterPlacementError as exc:
        raise GameRuleError(str(exc)) from exc


def is_source_counter_placement_event(event, response):
    summary = response.get("cost_summary")
    prefix = _source_placement_event_id(response.get("_mana_payment_id"), response.get("source"),
                                       response.get("ability"), "")
    return (event.event_id.startswith(prefix)
            or isinstance(summary, Mapping) and SOURCE_COUNTER_PLACEMENT_OFFER_FIELD in summary)


def source_counter_placement_event_is_valid(batch, effects, response, *, seat):
    from ...replacement.ordering import counter_cost_replacement_progress_matches

    event, = batch.events
    summary = response.get("cost_summary")
    if (not isinstance(summary, Mapping)
            or summary.get(SOURCE_COUNTER_PLACEMENT_OFFER_FIELD) != SOURCE_COUNTER_PLACEMENT_COST.to_dict()
            or response.get("cost_cards") or response.get("cost_objects")):
        return False
    payment_id, source_ref, ability_id = (response.get(key) for key in ("_mana_payment_id", "source", "ability"))
    if not all(type(value) is str and value for value in (payment_id, source_ref, ability_id)):
        return False
    payload = event.payload
    subject = event.affected_object
    logical_id = payload.get("target_logical_object_id")
    if (subject is None or subject.controller != seat
            or event.children or event.entry_scope is not None or event.affected_player is not None
            or type(logical_id) is not str or not logical_id.startswith(f"{subject.object_id}@")
            or response.get(SOURCE_COUNTER_PLACEMENT_ORIGIN) != {
                "object_id": subject.object_id, "ref": source_ref, "logical_id": logical_id,
            }
            or payload.get("target_controller") != seat or payload.get("target_zone") != "battlefield"
            or payload.get("prospective_subject") is True or payload.get("source") != source_ref
            or payload.get("counter_name") != SOURCE_COUNTER_PLACEMENT_COST.counter_name
            or type(payload.get("requested_amount")) is not int or payload["requested_amount"] != 1
            or event.event_id != _source_placement_event_id(payment_id, source_ref, ability_id, logical_id)):
        return False
    journal = response.get("_mana_replacement_selections", {})
    if not isinstance(journal, Mapping) or set(journal) - {event.event_id}:
        return False
    choices = journal.get(event.event_id, ())
    return (isinstance(choices, (list, tuple))
            and all(type(choice) is str and choice for choice in choices)
            and counter_cost_replacement_progress_matches(batch, effects, choices, 1))


__all__ = [
    "SourceCounterActivationCostError",
    "SourceCounterActivationCostHost",
    "commit_source_counter_removal_cost",
    "plan_source_counter_removal_cost",
    "plan_source_counter_removal_payment",
    "preview_source_counter_removal_cost",
    "source_counter_placement_is_payable",
    "pay_source_counter_placement",
    "freeze_source_counter_placement_response",
    "is_source_counter_placement_event",
    "source_counter_placement_event_is_valid",
]
