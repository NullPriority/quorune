"""Quote and verify the exact private hand selection before zone payment."""

from __future__ import annotations

from collections.abc import Mapping

from ...continuous_effect_model import Layer
from ...errors import GameRuleError
from ..casting_additional_costs import fixed_zone_change_cost_candidates
from ..colored_hand_credit import ColoredHandCredit, colored_hand_credit_from_cost_schema
from .model import CastProposalError


HAND_CREDIT_PAYMENT = "colored_hand_credit_payment"
HAND_CREDIT_CHOICE = "_colored_hand_credit_choice"
HAND_CREDIT_AMOUNT = "colored_hand_credit_amount"


def _eligible_hand_credit_refs(host, seat, card, spec):
    # This typed price reads color through CR 613.1e. Ability grants to the
    # candidates in hand do not participate in its predicate.
    return fixed_zone_change_cost_candidates(
        host, actor=seat, cost=spec.leaf, exclude_object_id=card.object_id,
        maximum_layer=Layer.COLOR,
    )


def bind_hand_credit(host, seat, card, option, cost_schema, response, *, hint):
    try:
        spec = colored_hand_credit_from_cost_schema(cost_schema)
    except ValueError:
        return False
    if spec is None:
        return True
    eligible = _eligible_hand_credit_refs(host, seat, card, spec)
    option[HAND_CREDIT_CHOICE] = {
        "type": "object_ref_array", "minimum": 0, "maximum": len(eligible), "legal_refs": list(eligible),
        "zone": "hand", "destination": "exile", "payment": "exile", "generic_credit_per_card": spec.credit_per_card,
    }
    if hint:
        option[HAND_CREDIT_AMOUNT] = spec.credit_per_card * len(eligible)
        return True
    chosen = response.get("exile_cards", ())
    if (not isinstance(chosen, (list, tuple))
            or any(type(ref) is not str or ref not in eligible for ref in chosen)
            or len(set(chosen)) != len(chosen)):
        return False
    entries = tuple(host._resolve_object(seat, ref, zones={"hand"}, owned_only=True) for ref in chosen)
    option[HAND_CREDIT_PAYMENT] = {"schema_version": 1, "spec": spec.to_dict(), "objects": [
        {"object_id": current.object_id, "logical_object_id": current.logical_object_id, "ref": current.ref}
        for current in entries
    ]}
    option[HAND_CREDIT_AMOUNT] = spec.credit_per_card * len(entries)
    return True


def hand_credit_reserved_ids(option):
    return {row["object_id"] for row in option.get(HAND_CREDIT_PAYMENT, {}).get("objects", ())}


def bind_hand_credit_choice(choice_schema, option):
    preview = option.pop(HAND_CREDIT_CHOICE, None)
    if preview is not None:
        choice_schema["exile_cards"] = preview


def selected_hand_credit_objects(host, proposal, card, program, option):
    """Validate the sealed selection at payment without repricing the announced cast."""
    spec = colored_hand_credit_from_cost_schema(getattr(program, "cost_schema", None))
    raw = option.get(HAND_CREDIT_PAYMENT)
    if raw is None and spec is None:
        return ()
    if (spec is None or not isinstance(raw, Mapping) or set(raw) != {"schema_version", "spec", "objects"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or spec != ColoredHandCredit.from_dict(raw["spec"])
            or not isinstance(raw["objects"], (tuple, list))
            or type(option.get(HAND_CREDIT_AMOUNT)) is not int
            or option[HAND_CREDIT_AMOUNT] != spec.credit_per_card * len(raw["objects"])):
        raise CastProposalError("The colored hand-exile payment changed", reason="stale_hand_credit")
    eligible = set(_eligible_hand_credit_refs(host, proposal.seat, card, spec))
    selected = []
    seen = set()
    for row in raw["objects"]:
        if (not isinstance(row, Mapping) or set(row) != {"object_id", "logical_object_id", "ref"}
                or any(type(value) is not str or not value for value in row.values())
                or row["ref"] in seen or row["ref"] not in eligible):
            raise CastProposalError("A selected hand credit payment is unavailable", reason="stale_hand_credit")
        obj = host._resolve_object(proposal.seat, row["ref"], zones={"hand"}, owned_only=True)
        if (obj.object_id, obj.logical_object_id) != (row["object_id"], row["logical_object_id"]):
            raise CastProposalError("A selected hand credit object changed zones", reason="stale_hand_credit")
        seen.add(row["ref"])
        selected.append(obj)
    return tuple(selected)


def commit_hand_credit_payment(host, proposal, card, objects, result):
    """Commit one group and defer its zone triggers until the cast is announced."""
    from copy import deepcopy
    from ...death_history_names import capture_departure_rules_names
    from ...zone_transitions import ZoneTransitionOwner
    from ...zone_trigger_events import ZoneTransitionKind
    from .spell_context import fixed_cast_cost_object_context

    departures = []
    for current in objects:
        data = capture_departure_rules_names(host, current, deepcopy(host._effective_card_data(current)))
        departures.append((current.zone, current.controller, current.logical_object_id, data))
        result.paid_contexts.append(fixed_cast_cost_object_context(host, current))
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        [(current.object_id, "exile") for current in objects],
        reason=f"{card.printed_name} colored hand cost", publish_events=False,
    )
    if tuple(current.object_id for current in moved) != tuple(current.object_id for current in objects):
        raise GameRuleError("The colored hand payment did not complete its sealed selected set")
    for current, (origin, controller, logical_id, data) in zip(moved, departures, strict=True):
        result.pre_recorded_zone_history_ids.add(current.object_id)
        result.paid_refs.append(current.ref)
        result.deferred_events.append((
            current, origin, controller, logical_id, data, [], current.zone, ZoneTransitionKind.ORDINARY,
        ))
        host._log(proposal.seat, "cost.exile", f"{proposal.seat} exiled {current.ref} to pay the cast cost.",
                  {"spell": card.ref, "object": current.ref}, importance=1,
                  changed_objects=[current.object_id], changed_players=[proposal.seat])
