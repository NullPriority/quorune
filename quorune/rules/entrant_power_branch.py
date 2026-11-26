from __future__ import annotations

"""One original entering creature supplies the power for a fixed resolution branch."""

from collections.abc import Mapping

from ..event_counter_reference import event_object_identity
from ..object_query import exact_numeric_characteristic
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects
from ..trigger_processing import pin_trigger_context_entry


ENTRANT_POWER_BRANCH_CAPABILITY = "resolution.effect.entrant_power_branch"
ENTRANT_POWER_BRANCH_FIELD = "entrant_power_branch_version"
ENTRANT_POWER_CONTEXT = "entrant_power_before_departure"
OPERATION = "apply_if_public_condition"
CONTROLLED_CREATURE_ENTRY = {"field": "controller", "op": "eq", "value": "$source.controller"}


def uses_entrant_power_branch(effect):
    return isinstance(effect, Mapping) and ENTRANT_POWER_BRANCH_FIELD in effect


def entrant_power_branch_effect(minimum, count):
    return {
        "op": OPERATION, ENTRANT_POWER_BRANCH_FIELD: 1, "player": "$controller",
        "minimum": minimum, "counter_count": count,
    }


def _branch_parts(effect, *, actor="$controller", bound=False):
    fields = {"op", ENTRANT_POWER_BRANCH_FIELD, "player", "minimum", "counter_count"}
    if bound:
        fields |= {"power", "entrant", "source"}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != OPERATION or type(effect[ENTRANT_POWER_BRANCH_FIELD]) is not int
            or effect[ENTRANT_POWER_BRANCH_FIELD] != 1 or effect["player"] != actor
            or type(effect["minimum"]) is not int or not 1 <= effect["minimum"] <= 9
            or type(effect["counter_count"]) is not int or not 1 <= effect["counter_count"] <= 3):
        raise ValueError("Entrant power requires the complete fixed draw or counter branch")
    return effect["minimum"], effect["counter_count"]


def entrant_power_branch_capabilities(effect):
    _branch_parts(effect)
    return (
        ENTRANT_POWER_BRANCH_CAPABILITY, "resolution.effect.public_condition",
        "trigger.event.normalized_zone_change", "trigger.effect.fixed_event", "trigger.placement.apnap",
        "zone.draw.library_to_hand", "counter.producer.fixed_event_object", "counter.producer.fixed_effect",
    )


def entrant_power_branch_runtime_dependencies(program):
    if not any(uses_entrant_power_branch(effect) for effect in nested_effects((*program.effects, *program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("creature.enter", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or len(program.effects) != 1 or program.event_condition != CONTROLLED_CREATURE_ENTRY):
        raise ValueError("Entrant power requires its original untargeted controlled creature entry")
    return entrant_power_branch_capabilities(program.effects[0])


def _entry_identity(event, facts):
    occurrence = event_object_identity(event, facts)
    identity = occurrence["logical_object_id"]
    object_id, separator, incarnation = identity.rpartition("@")
    counter = facts.get("card_zone_change_counter")
    if (event != "creature.enter" or facts.get("to") != "battlefield"
            or "creature" not in facts.get("types", ()) or not all((object_id, separator, incarnation))
            or type(counter) is not int or counter < 0 or str(counter) != incarnation):
        raise ValueError("Entrant power requires a committed creature entry")
    return object_id, identity, occurrence["reference"]


def entrant_power_trigger_context(program, event, facts):
    if not any(uses_entrant_power_branch(effect) for effect in program.effects):
        return {}
    _entry_identity(event, facts)
    return {ENTRANT_POWER_CONTEXT: None}


def _current_entrant(card, identity, reference):
    return (card is not None and card.zone == "battlefield" and not card.phased_out
            and card.logical_object_id == identity and card.ref == reference)


def _power(host, card):
    value = exact_numeric_characteristic(card, host._effective_card_data(card), "power")
    return value if value is not None else 0


def pin_entrant_power_departures(host, cards, *, error_type):
    departing = {card.object_id: card for card in cards if card.zone == "battlefield" and not card.phased_out}
    if not departing:
        return

    def capture(context):
        object_id, identity, reference = _entry_identity(context.get("event"), context)
        card = departing.get(object_id)
        if _current_entrant(card, identity, reference):
            return {"logical_object_id": identity, "power": _power(host, card)}
        return None

    try:
        pin_trigger_context_entry(host, ENTRANT_POWER_CONTEXT, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def bind_entrant_power_branch(host, effect, item, *, resolve):
    _branch_parts(effect)
    if item.kind != "triggered_ability" or item.targets or ENTRANT_POWER_CONTEXT not in item.context:
        raise ValueError("Entrant power lost its original untargeted ability")
    object_id, identity, reference = _entry_identity(item.context.get("event"), item.context)
    card = host.state.cards.get(object_id)
    current = _current_entrant(card, identity, reference)
    if current:
        power = _power(host, card)
    else:
        snapshot = item.context[ENTRANT_POWER_CONTEXT]
        if (not isinstance(snapshot, Mapping) or set(snapshot) != {"logical_object_id", "power"}
                or snapshot["logical_object_id"] != identity or type(snapshot["power"]) is not int):
            raise ValueError("Entrant power requires last-known information for the original object")
        power = snapshot["power"]
    return {**effect, "player": item.controller, "entrant": reference if current else None,
            "power": power, "source": resolve("$source")}


def resolved_entrant_power_branch(effect, actor, stack_controller):
    minimum, count = _branch_parts(effect, actor=actor, bound=True)
    entrant, source = effect["entrant"], effect["source"]
    if (actor != stack_controller or type(effect["power"]) is not int
            or entrant is not None and (type(entrant) is not str or not entrant or entrant.startswith("$"))
            or type(source) is not str or not source or source.startswith("$")):
        raise ValueError("Entrant power requires sealed public facts and the resolving controller")
    if effect["power"] >= minimum:
        return (FrozenMap({"op": "draw", "player": actor, "count": 1, "private": True}),)
    if entrant is None:
        return ()
    return (FrozenMap({"op": "place_counters", "card": entrant, "counter": "+1/+1",
                       "amount": count, "source": source}),)
