"""Closed identities for an activated-ability grant from linked exile."""

from typing import Mapping


LINKED_EXILE_ACTIVATION_CAPABILITY = "continuous.ability.linked_exile_activations"
LINKED_EXILE_ACTIVATION_HANDLER = "continuous.ability.linked-exile-activated.v1"
LINKED_EXILE_ACTIVATION_EVENT = "characteristics.evaluate"
LINKED_ACTIVATION_BINDING = "linked_activation_binding"
LINKED_ACTIVATION_PAIR = "linked_activation_pair"


def validate_grant_pair(pair: str) -> str:
    from .linked_exile_return_model import validate_linked_ability_binding
    from .replacement.face_status_copy import CopyExpiry

    if type(pair) is not str:
        raise ValueError("A linked activation grant requires its exact ability pair")
    domain, separator, remainder = pair.partition(":")
    identity, suffix, copy = remainder.partition(":")
    validate_linked_ability_binding(domain + separator + identity)
    if suffix:
        scope, delimiter, timestamp = copy.partition(":")
        if (scope not in {"permanent", *(item.value for item in CopyExpiry)} or not delimiter
                or not timestamp.isascii() or not timestamp.isdecimal()
                or (len(timestamp) > 1 and timestamp.startswith("0"))):
            raise ValueError("A linked activation grant requires its exact copied pair")
    return pair


def grant_descriptor(binding: str) -> dict:
    from .linked_exile_return_model import validate_linked_ability_binding

    validate_linked_ability_binding(binding)
    return {"handler_id": LINKED_EXILE_ACTIVATION_HANDLER, "schema_version": 1,
            "event": LINKED_EXILE_ACTIVATION_EVENT, "binding_id": binding}


def descriptor_binding(descriptor: Mapping) -> str:
    if not isinstance(descriptor, Mapping) or type(descriptor.get("schema_version")) is not int:
        raise ValueError("A linked activation grant requires a versioned descriptor")
    binding = descriptor.get("binding_id")
    if dict(descriptor) != grant_descriptor(binding):
        raise ValueError("A linked activation grant has a closed descriptor")
    return binding
