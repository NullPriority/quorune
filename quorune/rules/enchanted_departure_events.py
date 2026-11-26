from __future__ import annotations

"""Observe the original Aura attachment on an actual public departure."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import FrozenMap


ENCHANTED_DEPARTURE_EVENT = "permanent.leave"
ENCHANTED_DEPARTURE_VARIANT = "enchanted_permanent_graveyard_or_exile"
_ENCHANTED_DEPARTURE_ZONES = ("graveyard", "exile")


def enchanted_departure_condition():
    return {"all": [
        {"field": "attachments", "op": "contains_any", "value": ["$source.ref"]},
        {"any": [
            {"field": "to", "op": "eq", "value": zone}
            for zone in _ENCHANTED_DEPARTURE_ZONES
        ]},
    ]}


def enchanted_departure_context(host, context, occurrence, parts):
    from .enchanted_departure_return import (
        ENCHANTED_DEPARTURE_ARRIVALS, ENCHANTED_DEPARTURE_RETURN_CAPABILITY, ENCHANTED_DEPARTURE_SCOPE,
    )
    from .event_subscriptions import event_has_capability_subscriber

    if (
        occurrence.origin != "battlefield" or occurrence.destination not in _ENCHANTED_DEPARTURE_ZONES
        or not occurrence.previous_attachments
        or not event_has_capability_subscriber(host, ENCHANTED_DEPARTURE_EVENT, ENCHANTED_DEPARTURE_RETURN_CAPABILITY)
    ):
        return context
    group = occurrence.composition
    if group is None:
        members = (occurrence,)
    else:
        if not group.permanent_departure or len(parts) != group.member_count or any(
            part.composition is None or not part.composition.physical_arrival or part.composition.parent != group.parent
            for part in parts
        ):
            raise ValueError("Enchanted departure requires every committed physical arrival")
        members = parts
    rows = [
        {"object_id": part.object_id, "reference": part.card_ref,
         "zone_change_counter": part.zone_change_counter, "owner": part.owner, "zone": part.destination}
        for part in members if part.destination in _ENCHANTED_DEPARTURE_ZONES and part.card_object and not part.token
    ]
    return {
        **context, ENCHANTED_DEPARTURE_ARRIVALS: rows,
        ENCHANTED_DEPARTURE_SCOPE: {
            "object_id": occurrence.object_id,
            "reference": occurrence.card_ref,
            "previous_logical_object_id": occurrence.previous_logical_object_id,
        },
    }


def enchanted_departure_observer_context(source, program, event, occurrence_context):
    from .enchanted_departure_return import (
        ENCHANTED_DEPARTURE_OBSERVER, ENCHANTED_DEPARTURE_RETURN_CAPABILITY, ENCHANTED_DEPARTURE_SCOPE,
    )

    if ENCHANTED_DEPARTURE_RETURN_CAPABILITY not in program.capability_dependencies:
        return {}
    scope = occurrence_context.get(ENCHANTED_DEPARTURE_SCOPE)
    if (
        event != ENCHANTED_DEPARTURE_EVENT or scope is None
        or source.attached_to != scope["object_id"]
        or source.ref not in occurrence_context.get("attachments", ())
        or occurrence_context.get("card") != scope["reference"]
        or occurrence_context.get("card_object_identity") != scope["previous_logical_object_id"]
    ):
        raise ValueError("Enchanted departure lost the original attached permanent")
    return {ENCHANTED_DEPARTURE_OBSERVER: {
        "object_id": source.object_id, "logical_object_id": source.logical_object_id,
        "reference": source.ref, "controller": source.controller,
    }}


def enchanted_departure_return_node_capabilities(effects, target_schema):
    from .enchanted_departure_return import (
        ENCHANTED_DEPARTURE_RETURN_CAPABILITY, validate_enchanted_departure_return,
    )

    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        validate_enchanted_departure_return(effects[0])
    except (TypeError, ValueError):
        return ()
    return (
        ENCHANTED_DEPARTURE_RETURN_CAPABILITY,
        "attachment.aura.typed_restriction",
        "attachment.reference.current_or_lki",
        "trigger.effect.fixed_event",
        "trigger.event.normalized_zone_change",
        "trigger.placement.apnap",
        "zone.change.destination_replacement",
    )


def enchanted_departure_return_runtime_dependencies(program):
    from ..card_programs.admission import program_has_declared_complete_source_admission
    from .effect_shape_walkers import nested_effects
    from .enchanted_departure_return import ENCHANTED_DEPARTURE_RETURN_MECHANIC, is_enchanted_departure_return

    if ENCHANTED_DEPARTURE_RETURN_MECHANIC not in program.coverage and not any(
        is_enchanted_departure_return(effect) for effect in nested_effects((program.effects, program.handlers))
    ):
        return ()
    required = enchanted_departure_return_node_capabilities(program.effects, program.target_schema)
    if (
        not required or program.event != ENCHANTED_DEPARTURE_EVENT or program.active_zone != "battlefield"
        or program.cost_schema is not None or program.handlers
        or FrozenMap(program.event_condition or {}) != FrozenMap(enchanted_departure_condition())
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or not program_has_declared_complete_source_admission(program)
    ):
        raise ValueError("Enchanted departure return requires its complete Aura and sealed departure")
    return required
