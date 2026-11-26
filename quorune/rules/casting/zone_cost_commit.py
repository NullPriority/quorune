"""Commit selected zone changes through the existing cast transaction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...additional_cost_vocabulary import ZONE_CHANGE_COST_KIND
from ...zone_trigger_events import ZoneTransitionKind
from ..action_proposals import CastProposal
from ..casting_additional_costs import (
    AdditionalCostError,
    FixedZoneChangeAdditionalCost,
    announced_cost_x,
    fixed_zone_change_additional_cost,
    fixed_zone_change_cost_candidates,
)
from .model import CastProposalError

if TYPE_CHECKING:
    from .commit import CastCommitHost
from .spell_context import capture_paid_sacrifice_facts, fixed_cast_cost_object_context, require_paid_sacrifice_facts


def _resolve_fixed_zone_change_additional_cost(
    host: CastCommitHost,
    proposal: CastProposal,
    response: Mapping[str, Any],
    selected_option: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> tuple[
    tuple[Any, ...],
    FixedZoneChangeAdditionalCost,
    Mapping[str, tuple[str | None | Mapping[str, Any], ...]],
]:
    raw_costs = selected_option.get("additional_costs")
    cost_position = selected.get("cost_position")
    common_fields = {"kind", "operation", "cost_position"}
    reference_field = "card" if "card" in selected else "cards"
    if (
        not isinstance(raw_costs, list)
        or type(cost_position) is not int
        or cost_position < 0
        or cost_position >= len(raw_costs)
        or set(selected) != common_fields | {reference_field}
        or selected.get("kind") != ZONE_CHANGE_COST_KIND
    ):
        raise CastProposalError(
            "The zone-change additional-cost selection is malformed",
            reason="additional_cost_malformed",
        )
    try:
        cost = fixed_zone_change_additional_cost(raw_costs[cost_position])
    except AdditionalCostError as exc:
        raise CastProposalError(
            str(exc), reason="additional_cost_malformed"
        ) from exc
    from ..fixed_payment_groups import fixed_color_tap_zone_costs, fixed_discard_sacrifice_order, fixed_life_zone_costs
    from ...nonmana_kicker import distinct_kicker_zone_costs
    from .additional_zone_costs import granted_escape_zone_costs

    try:
        granted_group = granted_escape_zone_costs(selected_option, raw_costs)
    except (AdditionalCostError, TypeError, ValueError) as exc:
        raise CastProposalError(str(exc), reason="additional_cost_malformed") from exc
    if cost is None or (len(raw_costs) != 1 and cost_position not in dict(granted_group)
            and not fixed_life_zone_costs(raw_costs) and not distinct_kicker_zone_costs(selected_option,raw_costs)
            and not fixed_color_tap_zone_costs(raw_costs)
            and not fixed_discard_sacrifice_order(selected_option, raw_costs)):
        raise CastProposalError(
            "The zone-change additional cost is outside the represented family",
            reason="additional_cost_unsupported",
        )
    declared_x = selected_option.get('announced_additional_x')
    if cost.count_parameter is not None and announced_cost_x(response.get('x')) != declared_x:
        raise CastProposalError('Declared additional-cost X changed before commit', reason='stale_additional_x')
    count = cost.required_count(declared_x)
    if selected.get("operation") != cost.operation:
        raise CastProposalError(
            "The zone-change additional-cost operation is malformed",
            reason="additional_cost_malformed",
        )
    raw_refs = (
        [selected.get("card")]
        if reference_field == "card"
        else selected.get("cards")
    )
    candidates = fixed_zone_change_cost_candidates(
            host,
            actor=proposal.seat,
            cost=cost,
            exclude_object_id=proposal.object_id,
        )
    from .additional_zone_costs import ANNOUNCED_PITCH_X, announced_pitch_candidates
    if ANNOUNCED_PITCH_X in selected_option and (
        type(response.get("x")) is not int or response["x"] != selected_option[ANNOUNCED_PITCH_X]
    ):
        raise CastProposalError("Announced pitch X changed before commit", reason="stale_pitch_x")
    candidates = announced_pitch_candidates(host, proposal.seat, selected_option, candidates)
    if (
        not isinstance(raw_refs, (list, tuple))
        or len(raw_refs) != count
        or len(set(raw_refs)) != count
        or any(type(ref) is not str or ref not in candidates for ref in raw_refs)
    ):
        raise CastProposalError(
            "The selected zone-change cost object is no longer legal",
            status="unpayable",
            reason="zone_change_cost_unpayable",
        )
    paid = tuple(
        host._resolve_object(
            proposal.seat,
            ref,
            zones={cost.origin_zone},
            controlled_only=cost.origin_zone == "battlefield",
            owned_only=cost.origin_zone != "battlefield",
        )
        for ref in raw_refs
    )
    raw_journal = response.get("_mana_replacement_selections") or {}
    if not isinstance(raw_journal, Mapping) or len(raw_journal) > count:
        raise CastProposalError(
            "The casting replacement journal is malformed",
            reason="replacement_journal_malformed",
        )
    journals: dict[str, tuple[str | None | Mapping[str, Any], ...]] = {}
    for event_id, selections in raw_journal.items():
        matched = next(
            (
                card
                for card in paid
                if type(event_id) is str
                and event_id.startswith("zone.change:")
                and event_id.endswith(f":{card.ref}")
            ),
            None,
        )
        if (
            matched is None
            or not isinstance(selections, (list, tuple))
        ):
            raise CastProposalError(
                "The casting replacement journal is malformed",
                reason="replacement_journal_malformed",
            )
        journals[matched.ref] = tuple(selections)
    return paid, cost, journals


def commit_card_cost_changes(
    host, proposal, card, result, changes, *, required_paid_facts,
) -> None:
    """Apply selected cost moves and record their predeparture facts."""
    for (
        paid,
        origin,
        controller,
        logical_id,
        data,
        attachments,
        kind,
        destination,
        replacement_selections,
    ) in changes:
        from ...death_history_names import capture_departure_rules_names
        from ...sacrifice_prohibition_model import sacrifice_is_prohibited

        if kind == "sacrifice" and sacrifice_is_prohibited(host.state, paid):
            raise CastProposalError("A selected permanent cannot be sacrificed", reason="sacrifice_prohibited")
        data = capture_departure_rules_names(host, paid, data)
        if required_paid_facts and kind == "sacrifice":
            capture_paid_sacrifice_facts(host, paid, logical_id, data, result, required_paid_facts)
        result.paid_contexts.append(
            fixed_cast_cost_object_context(host, paid)
        )
        transition_kind = (
            ZoneTransitionKind.DISCARD
            if kind == "discard"
            else ZoneTransitionKind.SACRIFICE
            if kind == "sacrifice"
            else ZoneTransitionKind.ORDINARY
        )
        host.move_card(
            paid.object_id,
            destination,
            reason=f"{card.printed_name} {kind} cost",
            semantic_events=False,
            replacement_selections=replacement_selections,
            transition_kind=transition_kind,
        )
        result.paid_refs.append(paid.ref)
        result.deferred_events.append(
            (
                paid,
                origin,
                controller,
                logical_id,
                data,
                attachments,
                paid.zone,
                transition_kind,
            )
        )
        host._log(
            proposal.seat,
            f"cost.{kind}",
            f"{proposal.seat} paid {paid.ref} as a {kind} cost.",
            {"spell": card.ref, "object": paid.ref},
            importance=1,
            changed_objects=[paid.object_id],
            changed_players=[proposal.seat],
        )
    require_paid_sacrifice_facts(result, required_paid_facts)


def commit_ordered_zone_costs(
    host, proposal, response, card, result, selected_option, ordered, required_paid_facts, *, resolve_entries,
):
    """Resolve each selected object's current characteristics at its actual payment."""
    from ...replacement.ordering import ReplacementChoiceRequired
    from .ordered_zone_cost_frame import (
        ORDERED_ZONE_COST_FRAME_FIELD, OrderedZoneCostChoiceRequired, OrderedZoneCostFrame,
    )
    frame = OrderedZoneCostFrame(proposal.card_ref, proposal.cost_option_id, tuple(
        ("hand" if paid["operation"] == "discard_one" else "battlefield", paid["card"])
        for paid, _ in ordered
    ))
    if ORDERED_ZONE_COST_FRAME_FIELD in response:
        retained = OrderedZoneCostFrame.from_dict(response[ORDERED_ZONE_COST_FRAME_FIELD])
        retained.validate_response(response)
        if retained != frame:
            raise CastProposalError("The sealed cost order changed", reason="stale_additional_cost_order")
    for selected, scoped_response in ordered:
        moving = resolve_entries(host, proposal, scoped_response, selected_option, selected)
        paid_facts = required_paid_facts if selected["operation"] == "sacrifice_one" else set()
        try:
            commit_card_cost_changes(host, proposal, card, result, moving, required_paid_facts=paid_facts)
        except ReplacementChoiceRequired as required:
            raise OrderedZoneCostChoiceRequired(required, frame) from required
    require_paid_sacrifice_facts(result, required_paid_facts)


def _granted_escape_selected_groups(option):
    from .additional_zone_costs import GRANTED_ESCAPE_ORIGINS_FIELD, granted_escape_zone_costs

    prices = granted_escape_zone_costs(option, option.get("additional_costs"))
    selected = option.get("selected_additional_costs")
    if not prices or not isinstance(selected, (tuple, list)) or len(selected) != len(prices):
        raise ValueError("Granted Escape needs each announced zone payment")
    by_position = {}
    for payment in selected:
        if not isinstance(payment, Mapping):
            raise ValueError("Granted Escape payment must be typed")
        position = payment.get("cost_position")
        if type(position) is not int or position in by_position:
            raise ValueError("Granted Escape payment position changed")
        by_position[position] = payment
    ordered = []
    for position, cost in prices:
        payment = dict(by_position[position])
        raw_origins = payment.pop(GRANTED_ESCAPE_ORIGINS_FIELD, None)
        refs = (payment["card"],) if "card" in payment else tuple(payment.get("cards", ()))
        if (not isinstance(raw_origins, (list, tuple)) or len(raw_origins) != len(refs)
                or any(not isinstance(row, (list, tuple)) or len(row) != 3
                       or any(type(value) is not str or not value for value in row)
                       for row in raw_origins)
                or tuple(row[0] for row in raw_origins) != refs):
            raise ValueError("Granted Escape payment lost its announced incarnations")
        ordered.append((cost, payment, tuple(tuple(row) for row in raw_origins)))
    return ordered


def _granted_escape_cost_event_id(host, card, destination, kind):
    from ...semantic_runtime.zone_replacement_inputs import zone_change_snapshot_event
    from ...semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot

    transition_kind = {"discard": ZoneTransitionKind.DISCARD, "sacrifice": ZoneTransitionKind.SACRIFICE}.get(
        kind, ZoneTransitionKind.ORDINARY,
    )
    snapshot = capture_zone_change_replacement_snapshot(
        host, ((card.object_id, destination),), transition_kinds={card.object_id: transition_kind},
    )
    return zone_change_snapshot_event(snapshot, snapshot.subjects[0]).event_id


def _validate_granted_escape_payment(proposal, paid, origin, announced, arrivals):
    reference, original_incarnation, original_zone = announced
    expected_incarnation = (original_incarnation if original_zone == origin
                            else arrivals.get((reference, original_incarnation, original_zone)))
    if (paid is None or paid.object_id == proposal.object_id or paid.ref != reference or paid.zone != origin
            or paid.logical_object_id != expected_incarnation):
        raise CastProposalError("The granted Escape payment incarnation changed",
                               reason="stale_granted_escape_payment")
    if origin == "graveyard" and (paid.owner != proposal.seat or not paid.is_card_object or paid.is_token):
        raise CastProposalError("Granted Escape must exile other cards from the caster's graveyard",
                               reason="zone_change_cost_unpayable")


def commit_granted_escape_zone_costs(
    host, proposal, response, card, result, selected_option, required_paid_facts, *, resolve_entries,
):
    """Pay earlier costs first and revalidate the actual arrivals before exiling."""
    from ...cast_lifecycles import GRANTED_ESCAPE_USE_FIELD
    from ...replacement.model import ReplacementEffectError
    from ...replacement.ordering import ReplacementChoiceRequired
    from .ordered_zone_cost_frame import (
        ORDERED_ZONE_COST_FRAME_FIELD, OrderedZoneCostChoiceRequired, OrderedZoneCostFrame,
    )

    if GRANTED_ESCAPE_USE_FIELD not in selected_option:
        return False
    try:
        ordered = _granted_escape_selected_groups(selected_option)
        frame = OrderedZoneCostFrame(
            proposal.card_ref, proposal.cost_option_id,
            tuple((cost.origin_zone, ref) for cost, _, origins in ordered for ref, _, _ in origins),
            event_ids=(),
            payment_origins=tuple((logical_id, zone) for _, _, origins in ordered for _, logical_id, zone in origins),
            spell_incarnation=card.logical_object_id,
        )
        retained = None
        if ORDERED_ZONE_COST_FRAME_FIELD in response:
            retained = OrderedZoneCostFrame.from_dict(response[ORDERED_ZONE_COST_FRAME_FIELD])
            retained.validate_response(response)
            if retained.event_ids is None or replace(retained, event_ids=(), event_incarnations=()) != frame:
                raise ValueError("The sealed granted Escape payment order changed")
    except (KeyError, ReplacementEffectError, TypeError, ValueError) as exc:
        raise CastProposalError(str(exc), reason="stale_granted_escape_order") from exc
    raw_journal = response.get("_mana_replacement_selections") or {}
    if not isinstance(raw_journal, Mapping):
        raise CastProposalError("The granted Escape replacement journal is malformed",
                               reason="replacement_journal_malformed")
    zone_journal = {event_id: entries for event_id, entries in raw_journal.items()
                    if type(event_id) is str and event_id.startswith("zone.change:")}
    sealed_ids = {event_id for phase in retained.event_ids for event_id in phase} if retained is not None else set()
    if zone_journal.keys() - sealed_ids:
        raise CastProposalError("Granted Escape replacement choices require their exact payment events",
                               reason="replacement_journal_malformed")

    arrivals = {}
    event_ids = []
    event_incarnations = []
    for _, selected, origins in ordered:
        moving = resolve_entries(host, proposal, {**response, "_mana_replacement_selections": {}},
                                 selected_option, selected)
        if len(moving) != len(origins):
            raise CastProposalError("Granted Escape lost a selected payment", reason="stale_granted_escape_payment")
        for entry, announced in zip(moving, origins):
            paid, origin, _, _, _, _, kind, destination, _ = entry
            current = host.state.cards.get(paid.object_id)
            _validate_granted_escape_payment(proposal, current, origin, announced, arrivals)
            event_id = _granted_escape_cost_event_id(host, current, destination, kind)
            position = len(event_ids)
            sealed_phase = (retained.event_ids[position]
                            if retained is not None and position < len(retained.event_ids) else ())
            if sealed_phase and current.logical_object_id != retained.event_incarnations[position]:
                raise CastProposalError("The sealed granted Escape payment incarnation changed",
                                       reason="stale_granted_escape_order")
            event_ids.append(sealed_phase or (event_id,))
            event_incarnations.append(current.logical_object_id)
            journals = tuple(zone_journal.get(sealed_id, ()) for sealed_id in sealed_phase)
            if any(not isinstance(journal, (list, tuple)) for journal in journals):
                raise CastProposalError("The granted Escape replacement selections are malformed",
                                       reason="replacement_journal_malformed")
            selections = tuple(selection for journal in journals for selection in journal)
            paid_facts = required_paid_facts if kind == "sacrifice" else set()
            try:
                commit_card_cost_changes(host, proposal, card, result, ((*entry[:-1], tuple(selections)),),
                                         required_paid_facts=paid_facts)
            except ReplacementChoiceRequired as required:
                if len(required.batch.events) != 1 or required.batch.events[0].event_id != event_id:
                    raise CastProposalError("The pending granted Escape payment changed its event",
                                           reason="stale_granted_escape_order") from required
                if event_id not in event_ids[-1]:
                    event_ids[-1] = (*event_ids[-1], event_id)
                updated = replace(frame, event_ids=tuple(event_ids), event_incarnations=tuple(event_incarnations))
                raise OrderedZoneCostChoiceRequired(required, updated) from required
            if origin != "graveyard":
                arrived = host.state.cards.get(paid.object_id)
                if (arrived is not None and arrived.zone == "graveyard"
                        and arrived.owner == proposal.seat and arrived.is_card_object
                        and not arrived.is_token and arrived.logical_object_id != announced[1]):
                    arrivals[announced] = arrived.logical_object_id
    require_paid_sacrifice_facts(result, required_paid_facts)
    return True
