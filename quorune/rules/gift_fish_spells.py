"""Closed Gift a tapped Fish with exact promise-dependent results."""

from collections.abc import Mapping
from hashlib import sha256

from ..gift_card_model import GIFT_CARD_PROMISE_FIELD, GiftCardPromise, bind_gift_promise_target_schema
from ..linked_exile_return_model import (
    LINKED_EXILE_RETURN_CAPABILITY,
    LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec,
)
from ..replacement.immutable import thaw_value


GIFT_FISH_COST_FIELD = "gift_a_tapped_fish"
GIFT_FISH_TEMPLATE = "gift-tapped-fish-target-replacement-v1"
GIFT_FISH_CAPABILITY = "casting.additional_cost.gift_fish_target_replacement"
GIFT_FISH_COUNTER_RETURN_TEMPLATE = "gift-tapped-fish-linked-counter-return-v1"
GIFT_FISH_COUNTER_RETURN_VERSION = 2
GIFT_FISH_COUNTER_RETURN_CAPABILITY = "casting.additional_cost.gift_fish_counter_return"
GIFT_FISH_COUNTER_RETURN_DEPENDENCIES = (
    GIFT_FISH_COUNTER_RETURN_CAPABILITY, LINKED_EXILE_RETURN_CAPABILITY,
    "zone.change.destination_replacement", "target.permanent.characteristic_predicate",
    "target.revalidate_resolution", "token.creation.fixed_definition",
    "counter.producer.effect_entry", "counter.placement.quantity_replacement",
)
_RETURN_BINDING = "blink:" + sha256(GIFT_FISH_COUNTER_RETURN_TEMPLATE.encode("utf-8")).hexdigest()[:24]
GIFT_FISH_DEPENDENCIES = (
    GIFT_FISH_CAPABILITY, "permanent.return.owner_hand", "target.permanent.characteristic_predicate",
    "target.revalidate_resolution", "token.creation.fixed_definition",
)


def gift_bounce_effect():
    return {"op": "bounce", "card": "$target.0"}


def gift_fish_counter_return_effects():
    spec = LinkedExileReturnSpec(timing="next_end_step", entry_counters=(("+1/+1", 1),))
    common = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": _RETURN_BINDING}
    return (
        {**common, "phase": "exile", "cards": "$targets", "spec": spec.to_dict()},
        {**common, "phase": "return", "spec": spec.to_dict()},
    )


def gift_fish_target_schema(promised, *, version=1):
    if version == GIFT_FISH_COUNTER_RETURN_VERSION:
        return {"zones": ["battlefield"], "categories": ["permanent"],
                "types_any": ["creature"], "token": False, "count": 1}
    base = {"zones": ["battlefield"], "categories": ["permanent"],
            "controller_relation": "opponent", "count": 1}
    return {**base, **({"types_none": ["land"]} if promised else {"types_any": ["creature"]})}


def gift_fish_cost_schema(*, version=1):
    return {GIFT_FISH_COST_FIELD: {"schema_version": version,
                                  "unpromised": gift_fish_target_schema(False, version=version),
                                  "promised": gift_fish_target_schema(True, version=version)}}


def gift_fish_target_schema_for_program(program, promised):
    return gift_fish_target_schema(promised, version=program.cost_schema[GIFT_FISH_COST_FIELD]["schema_version"])


def gift_fish_runtime_dependencies(program):
    if program is None:
        return ()
    cost = program.cost_schema
    template = (program.provenance or {}).get("template_id")
    if not (isinstance(cost, Mapping) and GIFT_FISH_COST_FIELD in cost
            or template in {GIFT_FISH_TEMPLATE, GIFT_FISH_COUNTER_RETURN_TEMPLATE}):
        return ()
    value = cost.get(GIFT_FISH_COST_FIELD) if isinstance(cost, Mapping) else None
    version = value.get("schema_version") if isinstance(value, Mapping) else None
    if type(version) is not int or version not in {1, GIFT_FISH_COUNTER_RETURN_VERSION}:
        raise ValueError("Gift a tapped Fish requires an exact versioned cast and resolution form")
    counter_return = version == GIFT_FISH_COUNTER_RETURN_VERSION
    expected_effects = gift_fish_counter_return_effects() if counter_return else (gift_bounce_effect(),)
    if (
        thaw_value(cost) != gift_fish_cost_schema(version=version)
        or any(type(value[branch]["count"]) is not int for branch in ("unpromised", "promised"))
        or (program.event, program.active_zone) != ("resolve", "stack")
        or program.event_condition is not None or program.handlers
        or thaw_value(program.target_schema) != gift_fish_target_schema(False, version=version)
        or type(program.target_schema["count"]) is not int
        or tuple(thaw_value(program.effects)) != expected_effects
    ):
        raise ValueError("Gift a tapped Fish requires its original targets and complete resolution")
    if not counter_return:
        return GIFT_FISH_DEPENDENCIES
    from .linked_exile_return_shapes import linked_exile_return_node_capabilities

    required = linked_exile_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (LINKED_EXILE_RETURN_CAPABILITY not in required
            or program.trigger_limit is not None
            or not program.ability_id.startswith("spell:")
            or program.target_schema["token"] is not False
            or any(value[branch]["token"] is not False for branch in ("unpromised", "promised"))
            or any(type(effect["spec"]["entry_counters"][0][1]) is not int for effect in program.effects)):
        raise ValueError("Gift counter return requires the exact original linked exile and delayed counter return")
    return (*GIFT_FISH_COUNTER_RETURN_DEPENDENCIES, *required)


def is_closed_gift_fish_spell(program):
    try:
        required = gift_fish_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def bind_gift_fish_stack_schema(program, item, schema):
    if not gift_fish_runtime_dependencies(program):
        return schema
    return bind_gift_promise_target_schema(
        item, unpromised=gift_fish_target_schema_for_program(program, False),
        promised=gift_fish_target_schema_for_program(program, True),
    )


def prepare_gift_fish_resolution(host, item, effects, program):
    version = program.cost_schema[GIFT_FISH_COST_FIELD]["schema_version"]
    counter_return = version == GIFT_FISH_COUNTER_RETURN_VERSION
    expected = gift_fish_counter_return_effects() if counter_return else (gift_bounce_effect(),)
    if item.kind not in {"spell", "spell_copy"} or tuple(effects) != expected:
        raise ValueError("A tapped Fish Gift lost its original spell instruction")
    promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
    recipient = promise.recipient
    if recipient is None:
        return effects
    if recipient not in host.state.players:
        raise ValueError("A tapped Fish Gift lost the original chosen player")
    promised_effects = effects[:1] if counter_return else effects
    if recipient not in host.active_seats:
        return promised_effects
    fish = {
        "op": "create_token", "controller": recipient, "name": "Fish Token",
        "quantity": 1, "tapped": True,
        "characteristics": {
            "type_line": "Token Creature — Fish", "literal_token_name_version": 1,
            "colors": ["U"], "power": "1", "toughness": "1",
        },
    }
    return [fish, *promised_effects]
