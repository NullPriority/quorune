from __future__ import annotations

"""Closed resolution choice and turn-scoped play permission for an exiled card."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from .commander_return_choice import commander_return_price_is_closed
from .effect_shape_walkers import nested_effects
from .library_selection import LibrarySelectionObjectIdentity


CHOICE_FIELD = "choose_exiled_void_counter_card"
GRANT_FIELD = "chosen_void_counter_play"
PERMISSIONS_FIELD = "void_counter_play_permissions"
MECHANIC = "exiled-void-counter-card-play"
CAPABILITY = "zone.permission.opponent_void_counter_card_play"
DEPENDENCIES = (CAPABILITY, "zone.change.destination_replacement")


def choice_effect(actor="$controller", source="$source"):
    return {
        "op": "choose_objects", "player": actor, "source": source,
        CHOICE_FIELD: {"schema_version": 1},
    }


def is_choice_effect(effect, *, actor="$controller", source="$source"):
    marker = effect.get(CHOICE_FIELD)
    return (
        isinstance(marker, Mapping) and type(marker.get("schema_version")) is int
        and thaw_value(effect) == choice_effect(actor, source)
    )


def grant_effect(identity, *, source):
    return {
        "op": "grant_play_without_mana_cost", "source": source,
        GRANT_FIELD: {"schema_version": 1, "identity": identity.to_dict()},
    }


def grant_identity(effect):
    payload = effect.get(GRANT_FIELD)
    source = effect.get("source")
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"schema_version", "identity"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or set(effect) - {"reason", "_runtime_source"} != {"op", "source", GRANT_FIELD}
        or effect.get("op") != "grant_play_without_mana_cost"
        or type(source) is not str or not source
    ):
        raise ValueError("The chosen void-counter grant requires its versioned exile identity")
    return LibrarySelectionObjectIdentity.from_dict(payload["identity"])


def is_closed_price(cost):
    return commander_return_price_is_closed(cost) and cost["text"].casefold() == "{t}, sacrifice this creature"


def runtime_dependencies(program):
    if not any(
        CHOICE_FIELD in effect or GRANT_FIELD in effect
        for effect in nested_effects((program.effects, program.handlers))
    ):
        return ()
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, ActivatedAbilityCatalogHandler,
    )

    price = program.cost_schema
    if (
        (program.event, program.active_zone) != ("activate", "battlefield")
        or program.target_schema is not None or program.event_condition is not None
        or len(program.effects) != 1 or not is_choice_effect(program.effects[0])
        or not is_closed_price(price) or len(program.handlers) != 1
        or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    ):
        raise ValueError("The void-counter play choice requires its exact paid battlefield activation")
    descriptor = program.handlers[0]
    ActivatedAbilityCatalogHandler().validate(descriptor)
    ability = descriptor["ability"]
    if (
        ability.get("mana_ability") is not False or ability.get("target_schema") is not None
        or tuple(ability.get("zones") or ()) != ("battlefield",)
        or ability.get("cost_text") != price["text"]
        or any(thaw_value(ability.get(name)) != thaw_value(value)
               for name, value in price.items() if name != "text")
    ):
        raise ValueError("The void-counter play price differs from the activation catalog")
    return DEPENDENCIES


def is_closed_void_counter_play_program(program):
    try:
        required = runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def is_eligible_exile_card(card, actor, active_seats):
    return (
        card is not None and card.zone == "exile" and card.is_card_object
        and card.owner != actor and card.owner in active_seats
        and (not card.face_down or actor in card.known_to)
        and card.counters.get("void", 0) > 0
    )


def eligible_exile_card_refs(host, actor):
    if actor not in host.active_seats:
        return ()
    return tuple(sorted(
        card.ref for card in host.state.cards.values()
        if is_eligible_exile_card(card, actor, host.active_seats)
    ))
