from __future__ import annotations

"""One paid Room door unlock through the shared priority and payment owners."""

from dataclasses import replace
from typing import Any, Mapping

from ..errors import GameRuleError
from ..mana_undo import clear_mana_undo_stack
from ..shared_type_doors import (
    DOOR_UNLOCKED_EVENT,
    FULLY_UNLOCKED_EVENT,
    ROOM_UNLOCK_EVENT_CAPABILITY,
    SHARED_TYPE_FORM,
    SHARED_TYPE_FORM_CAPABILITY,
    SharedTypeDoors,
    printed_shared_type_faces,
    shared_type_doors_enabled,
)
from ..trigger_processing import enqueue_trigger_batch
from ..util import mana_cost_to_vector
from .action_proposals import ActionOffer, freeze_json


ROOM_UNLOCK_ACTION = "unlock_room"


def build_room_unlock_offers(host: Any, seat: str, card: Any) -> tuple[ActionOffer, ...]:
    from ..card_program_faces import current_shared_type_doors
    doors = current_shared_type_doors(card)
    if (not shared_type_doors_enabled(host.state) or doors is None or card.zone != "battlefield"
            or card.controller != seat or host.state.priority_player != seat
            or card.face_down or card.phased_out):
        return ()
    try:
        host._sorcery_timing(seat)
    except GameRuleError:
        return ()
    overrides = card.annotations.get("copy_overrides")
    if overrides is not None:
        from .room_copy_values import room_copy_unlock_costs
        faces = room_copy_unlock_costs(overrides)
    else:
        faces = printed_shared_type_faces(host.card_record(card))
        if not faces:
            return ()
        doors = doors.rebind(tuple(face["name"] for face in faces))
    return tuple(offer for face in faces if face["name"] in doors.locked
                 if (offer := _build_paid_offer(host, seat, card, face)) is not None)


def _build_paid_offer(host: Any, seat: str, card: Any, face: Mapping[str, Any]) -> ActionOffer | None:
    cost = face.get("mana_cost")
    if not cost:
        return None
    requirements, complex_symbols = mana_cost_to_vector(cost)
    if complex_symbols or not host._cost_is_affordable(seat, requirements, spend_context=None):
        return None
    name = face["name"]
    return ActionOffer(
        action_id=f"unlock-room:{card.ref}:{name}",
        action=ROOM_UNLOCK_ACTION,
        seat=seat,
        label=f"Unlock {name} for {cost}",
        expiry_revision=host.state.revision,
        payload=freeze_json({
            "kind": ROOM_UNLOCK_ACTION, "card": card.ref, "door": name,
            "room_incarnation": card.logical_object_id, "cost": cost,
            "requirements": requirements, "auto_pay": True,
        }),
    )


def commit_room_unlock(host: Any, *, seat: str, response: Mapping[str, Any]) -> None:
    host._check_priority(seat)
    ref, door = response.get("card"), response.get("door")
    if type(ref) is not str or not ref or type(door) is not str or not door:
        raise GameRuleError("A Room unlock requires a permanent and a door")
    card = host._resolve_object(seat, ref, zones={"battlefield"})
    offered = next((offer for offer in build_room_unlock_offers(host, seat, card)
                    if offer.to_dict()["door"] == door), None)
    if offered is None or response.get("room_incarnation") != card.logical_object_id:
        raise GameRuleError("The selected Room door cannot currently be unlocked")
    _validate_room_offer(host, offered, response)
    from ..card_program_faces import current_shared_type_doors
    prior = current_shared_type_doors(card)
    prepared = prior.unlock(door)
    clear_mana_undo_stack(host.state.players[seat].stats)
    spent, _ = host._pay_for_cost(seat, offered.to_dict()["requirements"], response, spend_context=None)
    current = host.state.cards.get(card.object_id)
    if (current is None or current.zone != "battlefield" or current.controller != seat
            or current.logical_object_id != response["room_incarnation"]
            or current_shared_type_doors(current) != prior or current.face_down or current.phased_out):
        raise GameRuleError("The Room changed while its unlock cost was being paid")
    current.shared_type_doors = prepared
    host._log(seat, "room.unlock_payment", f"{seat} paid to unlock {door}.", {
        "card": current.ref, "door": door, "payment": {key: value for key, value in spent.items() if value},
    }, changed_objects=[current.object_id], changed_players=[seat])
    batch = []
    collect_room_unlock_events(host, current, prior, prepared, actor=seat, trigger_batch=batch)
    enqueue_trigger_batch(host, batch)
    host.turn_priority.complete_special_action(seat)


def _validate_room_offer(host: Any, offer: ActionOffer, response: Mapping[str, Any]) -> None:
    supplied = response.get("proposal_fingerprint")
    if supplied is None:
        return
    expiry = response.get("expiry_revision")
    if (type(expiry) is not int or host.state.revision not in {expiry, expiry + 1}
            or supplied != replace(offer, expiry_revision=expiry).fingerprint):
        raise GameRuleError("The advertised Room unlock is stale")


def collect_room_unlock_events(
    host: Any, card: Any, before: SharedTypeDoors, after: SharedTypeDoors, *,
    actor: str, trigger_batch: list[Any],
) -> None:
    newly_unlocked = tuple(door for door in after.unlocked if door in before.locked)
    if not newly_unlocked:
        return
    context = {
        "card": card.ref, "card_object_id": card.object_id,
        "logical_object_id": card.logical_object_id, "controller": card.controller,
        "actor": actor,
    }
    for door in newly_unlocked:
        _publish_unlock(host, DOOR_UNLOCKED_EVENT, {**context, "door": door}, trigger_batch)
    if not after.locked:
        _publish_unlock(host, FULLY_UNLOCKED_EVENT, context, trigger_batch)


def _publish_unlock(host: Any, event: str, context: Mapping[str, Any], trigger_batch: list[Any]) -> None:
    actor = context["actor"]
    host._log(actor, event, f"{actor} unlocked {context.get('door') or context['card']}.",
              context, changed_objects=[context["card_object_id"]])
    host._dispatch_semantic_event(event, context, trigger_batch=trigger_batch)


def room_runtime_dependencies(program: Any) -> tuple[str, ...]:
    from .event_subscriptions import FixedEventSubscriptionSet

    condition = program.event_condition
    if isinstance(condition, Mapping) and "card_form_rule" in condition:
        descriptor = condition["card_form_rule"]
        if isinstance(descriptor, Mapping) and descriptor.get("kind") == SHARED_TYPE_FORM:
            _validate_shared_type_form(program, descriptor)
            return (SHARED_TYPE_FORM_CAPABILITY,)
    subscriptions = FixedEventSubscriptionSet.from_condition(condition)
    if program.event == DOOR_UNLOCKED_EVENT:
        face = program.provenance.get("face_id")
        expected = {"all": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"field": "actor", "op": "eq", "value": "$source.controller"},
            {"field": "door", "op": "eq", "value": face},
        ]}
        if type(face) is not str or not face or condition != expected or program.active_zone != "battlefield":
            raise ValueError("An own-door subscription requires its source, controller and printed face")
        return (ROOM_UNLOCK_EVENT_CAPABILITY,)
    if subscriptions is not None:
        full_unlock = subscriptions.subscription_for(FULLY_UNLOCKED_EVENT)
        if full_unlock is not None:
            return _validate_full_unlock_subscription(program, subscriptions, full_unlock)
    if program.event.removesuffix(".self") in {DOOR_UNLOCKED_EVENT, FULLY_UNLOCKED_EVENT}:
        raise ValueError("Room event requires its represented subscription")
    return ()


def _validate_full_unlock_subscription(program, subscriptions, full_unlock):
    seat = "$source.owner" if program.active_zone == "graveyard" else "$source.controller"
    actor = {"field": "actor", "op": "eq", "value": seat}
    if (program.event != "permanent.enter" or program.active_zone not in {"battlefield", "graveyard"}
            or len(subscriptions.subscriptions) != 2
            or subscriptions.subscription_for("permanent.enter") is None
            or full_unlock.to_dict()["condition"] != actor):
        raise ValueError("Full-unlock subscription requires independent entry and controller predicates")
    return (ROOM_UNLOCK_EVENT_CAPABILITY,)


def _validate_shared_type_form(program: Any, descriptor: Mapping[str, Any]) -> None:
    if (set(descriptor) != {"kind", "schema_version", "faces"}
            or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1
            or not isinstance(descriptor["faces"], (list, tuple))):
        raise ValueError("Shared type line form fields are malformed")
    doors = SharedTypeDoors(tuple(descriptor["faces"]))
    if (program.provenance.get("face_id") != doors.faces[0]
            or program.provenance.get("source_kind") != "type_line"
            or program.event != "intrinsic_ability" or program.active_zone != "all"
            or program.effects or program.handlers):
        raise ValueError("Shared type line form must be pinned to its first printed face")
