from __future__ import annotations

"""One original entering creature supplies the power for a fixed resolution branch."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..event_counter_reference import event_object_identity
from ..object_query import exact_numeric_characteristic
from ..public_characteristic_aggregates import greatest_power_creatures
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects
from ..trigger_processing import pin_trigger_context_entry


ENTRANT_POWER_BRANCH_CAPABILITY = "resolution.effect.entrant_power_branch"
ENTRANT_GREATEST_POWER_DRAW_CAPABILITY = "resolution.effect.entrant_greatest_power_draw"
ENTRANT_GREATEST_POWER_DRAW_TEMPLATE = "entrant-greatest-power-optional-draw-v2"
ENTRANT_POWER_BRANCH_FIELD = "entrant_power_branch_version"
ENTRANT_POWER_CONTEXT = "entrant_power_before_departure"
ENTRANT_CONTROLLER_LKI = "capture_entrant_controller_with_power"
OPERATION = "apply_if_public_condition"
CONTROLLED_CREATURE_ENTRY = {"field": "controller", "op": "eq", "value": "$source.controller"}
OTHER_CREATURE_ENTRY = {"field": "card", "op": "ne", "value": "$source.ref"}


def uses_entrant_power_branch(effect):
    return isinstance(effect, Mapping) and ENTRANT_POWER_BRANCH_FIELD in effect


def entrant_power_branch_effect(minimum, count):
    return {
        "op": OPERATION, ENTRANT_POWER_BRANCH_FIELD: 1, "player": "$controller",
        "minimum": minimum, "counter_count": count,
    }


def entrant_greatest_power_draw_effect():
    return {"op": OPERATION, ENTRANT_POWER_BRANCH_FIELD: 2, "player": "$controller"}


def _greatest_power_draw_parts(effect, *, actor="$controller", bound=False):
    fields = {"op", ENTRANT_POWER_BRANCH_FIELD, "player"}
    if bound:
        fields.add("recipient")
    if (set(effect) != fields or effect["op"] != OPERATION or effect["player"] != actor
            or type(effect[ENTRANT_POWER_BRANCH_FIELD]) is not int or effect[ENTRANT_POWER_BRANCH_FIELD] != 2):
        raise ValueError("Greatest-power draw requires the original entrant's complete optional draw")


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
    if effect.get(ENTRANT_POWER_BRANCH_FIELD) == 2:
        _greatest_power_draw_parts(effect)
        return (
            ENTRANT_GREATEST_POWER_DRAW_CAPABILITY, "resolution.effect.public_condition",
            "trigger.event.normalized_zone_change", "trigger.effect.fixed_event", "trigger.placement.apnap",
            "quantity_expression.public_characteristic_aggregate", "effect.choice.optional_fixed",
            "zone.draw.library_to_hand",
        )
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
            or len(program.effects) != 1 or not uses_entrant_power_branch(program.effects[0])):
        raise ValueError("Entrant power requires its original untargeted creature entry")
    effect = program.effects[0]
    greatest = effect[ENTRANT_POWER_BRANCH_FIELD] == 2
    if program.event_condition != (OTHER_CREATURE_ENTRY if greatest else CONTROLLED_CREATURE_ENTRY):
        raise ValueError("Entrant power requires its original creature entry predicate")
    if greatest and CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage:
        raise ValueError("Greatest-power draw requires current source ability discovery")
    return entrant_power_branch_capabilities(effect)


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
    effect = next((effect for effect in program.effects if uses_entrant_power_branch(effect)), None)
    if effect is None:
        return {}
    _entry_identity(event, facts)
    context = {ENTRANT_POWER_CONTEXT: None}
    if effect[ENTRANT_POWER_BRANCH_FIELD] == 2:
        context[ENTRANT_CONTROLLER_LKI] = True
    return context


def _current_entrant(card, identity, reference):
    return (card is not None and card.zone == "battlefield" and not card.phased_out
            and card.logical_object_id == identity and card.ref == reference)


def _power(host, card):
    value = exact_numeric_characteristic(card, host._effective_card_data(card), "power")
    return value if value is not None else 0


def _entry_creature_power(host, card):
    data = host._effective_card_data(card)
    types, _, _ = host._type_parts(data.get("type_line", ""))
    if "creature" not in types:
        return None
    power = exact_numeric_characteristic(card, data, "power")
    if type(power) is not int:
        raise ValueError("The original creature's public power is unavailable")
    return power


def pin_entrant_power_departures(host, cards, *, error_type):
    departing = {card.object_id: card for card in cards if card.zone == "battlefield" and not card.phased_out}
    if not departing:
        return

    def capture(context):
        object_id, identity, reference = _entry_identity(context.get("event"), context)
        card = departing.get(object_id)
        if _current_entrant(card, identity, reference):
            if context.get(ENTRANT_CONTROLLER_LKI) is True:
                return {"logical_object_id": identity, "power": _entry_creature_power(host, card),
                        "controller": card.controller}
            return {"logical_object_id": identity, "power": _power(host, card)}
        return None

    try:
        pin_trigger_context_entry(host, ENTRANT_POWER_CONTEXT, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def _greatest_power_draw_recipient(host, item, card, *, current, identity):
    if item.context.get(ENTRANT_CONTROLLER_LKI) is not True:
        raise ValueError("Greatest-power draw lost its entrant power and controller snapshot contract")
    if current:
        power, controller = _entry_creature_power(host, card), card.controller
    else:
        snapshot = item.context[ENTRANT_POWER_CONTEXT]
        if (not isinstance(snapshot, Mapping) or set(snapshot) != {"logical_object_id", "power", "controller"}
                or snapshot["logical_object_id"] != identity
                or snapshot["power"] is not None and type(snapshot["power"]) is not int
                or type(snapshot["controller"]) is not str or snapshot["controller"] not in host.state.players):
            raise ValueError("Greatest-power draw requires the original entrant's last-known power and controller")
        power, controller = snapshot["power"], snapshot["controller"]
    if power is None or controller not in host.active_seats:
        return None
    others = tuple(
        host._public_object_query_result(other, _maximum_layer=None)
        for other in host.state.cards.values()
        if other.zone == "battlefield" and not other.phased_out and other.logical_object_id != identity
    )
    greatest = greatest_power_creatures(others)
    return controller if not greatest or power > greatest[0].effective_power else None


def bind_entrant_power_branch(host, effect, item, *, resolve):
    greatest = effect.get(ENTRANT_POWER_BRANCH_FIELD) == 2
    if greatest:
        _greatest_power_draw_parts(effect)
    else:
        _branch_parts(effect)
    if item.kind != "triggered_ability" or item.targets or ENTRANT_POWER_CONTEXT not in item.context:
        raise ValueError("Entrant power lost its original untargeted ability")
    object_id, identity, reference = _entry_identity(item.context.get("event"), item.context)
    card = host.state.cards.get(object_id)
    current = _current_entrant(card, identity, reference)
    if greatest:
        recipient = _greatest_power_draw_recipient(host, item, card, current=current, identity=identity)
        return {**effect, "player": item.controller, "recipient": recipient}
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


def resolved_entrant_power_branch(effect, actor, stack_controller, *, active_seats=()):
    if effect.get(ENTRANT_POWER_BRANCH_FIELD) == 2:
        _greatest_power_draw_parts(effect, actor=actor, bound=True)
        recipient = effect["recipient"]
        if actor != stack_controller or recipient is not None and recipient not in active_seats:
            raise ValueError("Greatest-power draw requires the resolving controller and eligible entrant controller")
        if recipient is None:
            return ()
        return (FrozenMap({"op": "offer_optional_effect", "player": recipient,
                           "effects": [{"op": "draw", "player": recipient, "count": 1, "private": True}]}),)
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
