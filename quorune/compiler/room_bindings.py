from __future__ import annotations

"""Source-spanned Room form and unlock grammar over the existing event owners."""

import re
from typing import Any

from ..characteristic_evaluation import type_parts
from ..rules.event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
from ..shared_type_doors import (
    DOOR_UNLOCKED_EVENT, FULLY_UNLOCKED_EVENT, ROOM_UNLOCK_EVENT_CAPABILITY,
    SHARED_TYPE_FORM_CAPABILITY, printed_shared_type_faces,
)
from ..util import mana_cost_to_vector
from .fixed_public_event_trigger_bindings import _constellation_spec, _spec


ROOM_REMINDER = (
    "(You may cast either half. That door unlocks on the battlefield. "
    "As a sorcery, you may pay the mana cost of a locked door to unlock it.)"
)
OWN_DOOR_VARIANT = "source_room_door_unlock"
EERIE_VARIANT = "controlled_enchantment_entry_or_room_unlock"
_DOOR = re.compile(r"When you unlock this door, (?P<body>.+)", re.IGNORECASE)
_EERIE = re.compile(
    r"(?:Eerie\s+[\u2014-]\s+)?(?P<entry>Whenever .+? enters) and "
    r"whenever you fully unlock a Room, (?P<body>.+)", re.IGNORECASE,
)


def printed_room_form(record: Any) -> tuple[str, str] | None:
    faces = printed_shared_type_faces(record)
    if not faces:
        return None
    types, subtypes, _ = type_parts(faces[0]["type_line"])
    if types != {"enchantment"} or "room" not in subtypes:
        return None
    names = tuple(face.get("name") for face in faces)
    if (any(type(name) is not str or not name for name in names) or names[0] == names[1]
            or any(mana_cost_to_vector(face.get("mana_cost"))[1] for face in faces)):
        return None
    return names


def room_source_material(text: str) -> str:
    return re.sub(r"\bthis Room\b", "this enchantment", text, flags=re.IGNORECASE)


def own_door_binding_spec(text: str, face: str):
    match = _DOOR.fullmatch(text)
    if match is None:
        return None
    return _spec(
        DOOR_UNLOCKED_EVENT, OWN_DOOR_VARIANT, match["body"],
        "fixed-counter-public-room-door-trigger-v1", "cr-603-handling-triggered-abilities",
        condition={"all": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"field": "actor", "op": "eq", "value": "$source.controller"},
            {"field": "door", "op": "eq", "value": face},
        ]},
        capabilities=(SHARED_TYPE_FORM_CAPABILITY, ROOM_UNLOCK_EVENT_CAPABILITY),
    )


def own_door_binding(text: str, face: str | None):
    if face is None:
        return None
    from .fixed_counter_trigger_nodes import FixedCounterTriggerBinding, FixedCounterTriggerEvent
    from .qualified_zone_event_bindings import public_binding_from_spec

    return public_binding_from_spec(
        own_door_binding_spec(text, face),
        binding_type=FixedCounterTriggerBinding, event_type=FixedCounterTriggerEvent,
    )


def eerie_binding_spec(text: str, *, card_name: str | None = None):
    match = _EERIE.fullmatch(text)
    if match is None:
        return None
    entry = _constellation_spec(f"{match['entry']}, {match['body']}", card_name=card_name)
    if entry is None:
        return None
    subscriptions = (
        FixedEventSubscription(entry.event, entry.condition),
        FixedEventSubscription(FULLY_UNLOCKED_EVENT, {
            "field": "actor", "op": "eq", "value": "$source.controller",
        }),
    )
    return _spec(
        entry.event, EERIE_VARIANT, match["body"],
        "fixed-counter-public-multi-event-trigger-v1", "trigger-event-normalized-zone-change",
        condition=fixed_event_subscription_condition(subscriptions),
        capabilities=("trigger.subscription.fixed_multi_event", ROOM_UNLOCK_EVENT_CAPABILITY),
    )
