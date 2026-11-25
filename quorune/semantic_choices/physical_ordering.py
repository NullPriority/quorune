"""Shared private projection and command validation for physical departure orders."""

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from .model import OrderingChoice


def physical_order_schema(refs):
    return OrderingChoice(
        "cards", refs, visibility="actor_private",
        schema_extras=FrozenMap({"type": "object_ref_array", "shape": "ref_array", "order": "bottom_to_top"}),
    ).choice_schema()


def physical_order_payload(frame):
    choice = frame.current_choice
    if choice is None:
        raise GameRuleError("Physical departure ordering is already complete")
    (actor, destination, _position), refs = choice
    states = {row["ref"]: row for subject in frame.request.subjects for row in subject.physical_states}

    def name(ref):
        state = states[ref]
        if destination == "library" and state["face_down"] and actor not in (
            *state["known_to"], *state["revealed_to"],
        ):
            return "Face-down component"
        return state["printed_name"]

    previous = [
        {"player": group[0], "destination": group[1], "cards": list(order)}
        for (group, _refs), order in zip(frame.request.choices, frame.orders)
        if group[1] != "library"
    ]
    return {
        "prompt": "Order the departing physical cards from bottom to top.",
        "cards": [{"id": ref, "name": name(ref)} for ref in refs],
        "previous_orders": previous,
        "legal_actions": [{
            "id": "order", "action": "order", "label": "Confirm physical order",
            "choice_schema": physical_order_schema(refs),
        }],
    }


def physical_order_response(host, decision, frame):
    choice = frame.current_choice
    if choice is None:
        raise GameRuleError("Physical ordering is already complete")
    (actor, _destination, _position), refs = choice
    if (
        decision.kind != "permanent.composition_order" or decision.role != "pilot"
        or decision.actors != [actor] or actor not in host.active_seats or set(decision.responses) != {actor}
        or FrozenMap(decision.payload_by_actor) != FrozenMap({actor: physical_order_payload(frame)})
    ):
        raise GameRuleError("Physical ordering principal or issued payload changed")
    response = decision.responses[actor]
    if (
        set(response) - {"action", "cards", "choice_schema", "label"} or response.get("action") != "order"
        or "label" in response and response["label"] != "Confirm physical order"
        or "choice_schema" in response and FrozenMap(response["choice_schema"]) != FrozenMap(physical_order_schema(refs))
    ):
        raise GameRuleError("Physical order response differs from its issued schema")
    return response.get("cards")
