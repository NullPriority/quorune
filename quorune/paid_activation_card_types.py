"""Closed result and receipt for types among zero to three sacrificed permanents."""

from collections.abc import Mapping

from .replacement.immutable import thaw_value
from .type_subtype_vocabulary import CARD_TYPE_SUBTYPES
from .util import stable_json


COST_KIND = "sacrifice_up_to_three"
COST_TEXT = "{T}, Sacrifice up to three permanents"
RESULT_TEXT = (
    "If there were three or more card types among the sacrificed permanents, "
    "each opponent loses 3 life, you gain 3 life, and you draw three cards."
)
CONTEXT_FIELD = "activation_paid_sacrifice_card_types"
TEMPLATE = "activation-paid-sacrifice-card-types-v1"
MECHANIC = "activation-paid-sacrifice-card-types"
CAPABILITY = "resolution.effect.activation_paid_sacrifice_card_types"
CONDITION_KIND = "paid_activation_card_types"
REFERENCE_KIND = "paid_activation_card_types_value"
DEPENDENCY_MECHANICS = (
    "cr-119-life", "cr-101-the-magic-golden-rules", "cr-121-drawing-a-card",
)
_CONDITION = {"kind": CONDITION_KIND, "minimum": 3}
_REFERENCE = {"kind": REFERENCE_KIND, "schema_version": 1}


def has_bounded_sacrifice(ability):
    return any(choice.kind == COST_KIND for choice in ability.choices)


def exact_paid_type_price(cost):
    return stable_json(thaw_value(cost)) == stable_json({
        "text": COST_TEXT, "mana": dict.fromkeys(("B", "C", "G", "GENERIC", "R", "U", "W"), 0),
        "complex_symbols": [], "tap_source": True,
        "untap_source": False, "discard_source": False, "sacrifice_source": False,
        "exile_source": False, "life_payment": 0, "energy_payment": 0,
        "loyalty_delta": None, "source_counter_removal_cost": None,
        "choices": [{"k": COST_KIND, "n": 3, "z": "battlefield"}], "uncompiled_costs": [],
    })


def paid_type_instruction():
    from .paid_sacrifice_conditions import PAID_SACRIFICE_CONDITION_VERSION
    from .resolution_conditions import RESOLUTION_CONDITION_OPERATION

    return {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": PAID_SACRIFICE_CONDITION_VERSION,
        "player": "$controller", "condition": dict(_CONDITION), "cast_fact": dict(_REFERENCE),
        "effects": [
            {"op": "lose_life_each_opponent", "amount": 3},
            {"op": "life", "player": "$controller", "delta": 3},
            {"op": "draw", "player": "$controller", "count": 3, "private": True},
        ],
        "mechanic_ids": list(DEPENDENCY_MECHANICS), "prefix_mechanic_ids": [],
    }


def uses_paid_type_condition(effect):
    condition = effect.get("condition")
    return isinstance(condition, Mapping) and condition.get("kind") == CONDITION_KIND


def validate_paid_type_instruction(effect):
    expected = paid_type_instruction()
    actual = thaw_value(effect)
    if type(actual.get("cast_fact")) is bool:
        player = actual.get("player")
        if type(player) is not str or not player or player.startswith("$"):
            raise ValueError("Paid card types require the resolving controller")
        expected["player"] = player
        expected["cast_fact"] = actual["cast_fact"]
        for child in expected["effects"]:
            if child.get("player") == "$controller":
                child["player"] = player
    if actual != expected:
        raise ValueError("Paid card types require one exact ordered result")
    return actual["condition"], tuple(effect["effects"])


def paid_type_capabilities(*, effects, target_schema, mechanic_ids, cost_schema):
    from .resolution_conditions import RESOLUTION_CONDITION_MECHANIC

    if (target_schema is not None or not exact_paid_type_price(cost_schema)
            or not {MECHANIC, RESOLUTION_CONDITION_MECHANIC, *DEPENDENCY_MECHANICS} <= set(mechanic_ids)
            or thaw_value(effects) != [paid_type_instruction()]):
        return ()
    return (CAPABILITY, "activation.selected_zone_change.fixed", "life.change.effect", "zone.draw.library_to_hand")


def read_paid_type_receipt(value):
    if (not isinstance(value, Mapping)
            or set(value) != {"version", "ability_key", "source", "payer", "objects", "types"}
            or type(value["version"]) is not int or value["version"] != 1
            or any(type(value[key]) is not str or not value[key] for key in ("ability_key", "source", "payer"))):
        raise ValueError("Paid card type receipt is missing or malformed")
    objects, types = value["objects"], value["types"]
    if (not isinstance(objects, (tuple, list)) or len(objects) > 3
            or any(type(obj) is not str or not obj for obj in objects) or len(set(objects)) != len(objects)
            or not isinstance(types, (tuple, list))
            or any(type(card_type) is not str or card_type not in CARD_TYPE_SUBTYPES for card_type in types)
            or tuple(types) != tuple(sorted(set(types))) or (not objects and types)):
        raise ValueError("Paid card type receipt is not canonical")
    return value


def resolve_paid_type_condition(value, item):
    if thaw_value(value) != _REFERENCE:
        raise ValueError("Paid card type reference is malformed")
    receipt = read_paid_type_receipt(item.context.get(CONTEXT_FIELD))
    copy_source = item.context.get("copied_from_stack")
    copied = type(copy_source) is str and bool(copy_source)
    if (item.context.get("currently_resolving") is not True
            or item.context.get("source_logical_object_id") != receipt["source"]
            or item.semantic_key != receipt["ability_key"]
            or item.kind not in {"activated_ability", "activated_ability_copy"}
            or not copied and (item.kind != "activated_ability" or item.controller != receipt["payer"])):
        raise ValueError("Paid card types lost their originating activation")
    return len(receipt["types"]) >= 3
