"""Commit selected zone changes through the existing cast transaction."""

from __future__ import annotations

from ...zone_trigger_events import ZoneTransitionKind
from .model import CastProposalError
from .spell_context import capture_paid_sacrifice_facts, fixed_cast_cost_object_context, require_paid_sacrifice_facts


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
