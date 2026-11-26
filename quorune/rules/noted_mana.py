from __future__ import annotations

"""Bind one paid mana type to the original artifact incarnation."""

from collections.abc import Mapping
from dataclasses import replace

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, thaw_value


NOTED_MANA_CAPABILITY = "mana.production.last_noted_activation_type"
NOTED_MANA_ANNOTATION = "last_noted_mana_type"
NOTED_MANA_PAYMENT_FIELD = "paid_mana_type_to_note"
NOTED_MANA_EFFECT_FIELD = "note_paid_mana_type"
NOTED_MANA_TYPES = frozenset("WUBRGC")
CHARGE_COST_TEXT = "{1}, {T}"
SPEND_COST_TEXT = "{T}, Remove a charge counter from this artifact"
CHARGE_EFFECT_TEXT = (
    "put a charge counter on this artifact. note the type of mana spent to pay this activation cost. "
    "activate only if there are no charge counters on this artifact."
)
SPEND_EFFECT_TEXT = "add one mana of this artifact's last noted type."
_COUNTER_EFFECT = {
    "op": "place_counters", "card": "$source.zone_object",
    "counter": "charge", "amount": 1, "source": "$source",
    NOTED_MANA_EFFECT_FIELD: "$context." + NOTED_MANA_PAYMENT_FIELD,
}


def noted_mana_charge_effect():
    return dict(_COUNTER_EFFECT)


def noted_mana_cost_is_closed(cost, *, charge):
    if not isinstance(cost, Mapping):
        return False
    expected = {
        "text": CHARGE_COST_TEXT if charge else SPEND_COST_TEXT,
        "mana": {**dict.fromkeys("WUBRGC", 0), "GENERIC": int(charge)},
        "tap_source": True, "untap_source": False, "discard_source": False,
        "sacrifice_source": False, "exile_source": False,
        "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
        "complex_symbols": [], "choices": [], "uncompiled_costs": [],
        "source_counter_removal_cost": None if charge else {
            "schema_version": 1, "counter_name": "charge", "amount": 1,
        },
    }
    raw = thaw_value(FrozenMap(cost))
    return raw == expected and all(type(amount) is int for amount in raw["mana"].values())


def is_noted_mana_charge_ability(ability):
    from ..activation_condition_model import ActivationCondition, ActivationConditionKind
    from ..compiler.activated_costs import activated_ability_cost

    return (
        ability.zones == ("battlefield",) and not ability.mana_ability
        and not ability.sorcery_speed and ability.activation_limit is None and ability.target_schema is None
        and ability.activation_conditions == (ActivationCondition(ActivationConditionKind.SOURCE_NO_CHARGE_COUNTERS),)
        and " ".join(ability.effect_text.casefold().split()) == CHARGE_EFFECT_TEXT
        and noted_mana_cost_is_closed(activated_ability_cost(ability), charge=True)
    )


def capture_noted_mana_payment(program, spent, other):
    if program is None or not any(NOTED_MANA_EFFECT_FIELD in effect for effect in program.effects):
        return other
    if other is not None or any(key not in NOTED_MANA_TYPES or type(amount) is not int or amount < 0
                                for key, amount in spent.items()):
        raise GameRuleError("Noting mana requires its own canonical one-mana activation payment")
    paid = [key for key, amount in spent.items() if amount]
    if len(paid) != 1 or spent[paid[0]] != 1:
        raise GameRuleError("Noting mana requires one paid mana type")
    return NOTED_MANA_PAYMENT_FIELD, paid[0]


def current_noted_mana_type(source):
    value = source.annotations.get(NOTED_MANA_ANNOTATION)
    if not isinstance(value, Mapping) or set(value) != {"logical_object_id", "mana_type"}:
        return None
    color = value["mana_type"]
    if value["logical_object_id"] == source.logical_object_id and type(color) is str and color in NOTED_MANA_TYPES:
        return color
    return None


def lower_noted_mana_charge(handler, effect, context):
    from ..semantic_runtime.context import SemanticNodeError
    from ..semantic_runtime.intents import IntentPlan, SetCardDesignationIntent

    color = effect[NOTED_MANA_EFFECT_FIELD]
    if (type(color) is not str or color not in NOTED_MANA_TYPES
            or effect.get("counter") != "charge" or type(effect.get("amount")) is not int or effect["amount"] != 1
            or context.source is None):
        raise SemanticNodeError("Charging an artifact requires its original source and paid mana type")
    subject = effect.get("card")
    if subject is None:
        return IntentPlan(operation=handler.operation, handler_id=handler.handler_id, intents=(), result_shape="by_player")
    if subject != context.source.card_ref or effect.get("source") != subject:
        raise SemanticNodeError("The paid mana type belongs to the original resolving artifact")
    clean = {key: value for key, value in effect.items() if key != NOTED_MANA_EFFECT_FIELD}
    plan = handler.lower(clean, context)
    note = SetCardDesignationIntent(subject, NOTED_MANA_ANNOTATION, color, context.actor, context.default_reason)
    return replace(plan, intents=(*plan.intents, note), result_shape="by_player")


def noted_mana_program_dependencies(program):
    from ..abilities import ActivatedAbility
    from ..chosen_color_mana_abilities import ChosenColorActivatedManaAbilitySpec
    from ..chosen_color_mana_model import CHOSEN_COLOR_MANA_HANDLER_ID, NOTED_MANA_OUTPUT_VERSION
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    from .effect_shape_walkers import nested_effects

    charge = any(NOTED_MANA_EFFECT_FIELD in effect for effect in nested_effects(program.effects))
    producers = tuple(handler["ability"] for handler in program.handlers
                      if handler.get("handler_id") == CHOSEN_COLOR_MANA_HANDLER_ID
                      and handler["ability"]["output"].get("schema_version") == NOTED_MANA_OUTPUT_VERSION)
    if not charge and not producers and NOTED_MANA_CAPABILITY not in program.capability_dependencies:
        return ()
    catalogs = tuple(handler["ability"] for handler in program.handlers
                     if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID)
    if ((program.event, program.active_zone) != ("activate", "battlefield") or program.target_schema is not None
            or program.event_condition is not None or len(catalogs) != 1
            or not noted_mana_cost_is_closed(program.cost_schema, charge=charge)):
        raise ValueError("Noted mana requires its exact battlefield activation and catalog")
    ability = ActivatedAbility.from_dict(thaw_value(catalogs[0]))
    if charge:
        if producers or len(program.handlers) != 1 or tuple(program.effects) != (_COUNTER_EFFECT,) or not is_noted_mana_charge_ability(ability):
            raise ValueError("Charging an artifact requires the exact paid note and counter instruction")
        return NOTED_MANA_CAPABILITY, "counter.producer.fixed_effect"
    if len(producers) != 1 or len(program.handlers) != 2 or program.effects:
        raise ValueError("Stored mana requires its exact output handler")
    spec = ChosenColorActivatedManaAbilitySpec.from_dict(producers[0])
    if ability != replace(spec.to_activated_ability(), builtin_semantic_key=program.key):
        raise ValueError("Stored mana lost its exact output catalog")
    return (NOTED_MANA_CAPABILITY,)


def is_closed_noted_mana_charge_program(program):
    if tuple(program.effects) != (_COUNTER_EFFECT,):
        return False
    try:
        required = noted_mana_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
