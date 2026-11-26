"""One original opponent can pay a fixed resolution cost before a controller result."""

from __future__ import annotations

from collections.abc import Mapping

from ..fixed_effect_payment import (
    FIXED_EFFECT_PAYMENT_CAPABILITY, OPPONENT_SOURCE_POWER_MANA_PRICE_VERSION, FixedEffectPaymentSpec,
)
from ..public_history_query_model import PublicHistoryQuerySpec
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
from ..spell_history_ordinal_model import QUALIFIED_SPELL_ORDINAL_CAPABILITY, QualifiedSpellOrdinalSpec
from ..replacement.immutable import FrozenMap
from ..util import mana_cost_to_vector


OPPONENT_EVENT_MANA_PAYMENT_FIELD = "opponent_event_mana_payment_version"
OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY = "effect.choice.opponent_event_fixed_mana_payment"
OPPONENT_SOURCE_POWER_MANA_CAPABILITY = "effect.choice.opponent_source_power_mana_payment"
SOURCE_POWER_PRICE = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, "power")
_CAST_OPPONENT = {"field": "controller", "op": "ne", "value": "$source.controller"}
_CAST_CONDITIONS = (
    FrozenMap(_CAST_OPPONENT),
    FrozenMap({"all": [_CAST_OPPONENT, {"not": {"field": "types", "op": "contains_any", "value": ["creature"]}}]}),
)
_DRAW_CONDITION = FrozenMap({"field": "player", "op": "ne", "value": "$source.controller"})
_LOCALS = {"_choice_actor", "_legal_objects", "_stack_label"}


def uses_opponent_event_mana_payment(effect):
    return isinstance(effect, Mapping) and OPPONENT_EVENT_MANA_PAYMENT_FIELD in effect


def opponent_event_payment_conditions_match(event, condition, child):
    if not isinstance(condition, Mapping):
        return False
    if child.get("op") == "offer_draw":
        return event == "spell.cast" and FrozenMap(condition) in _CAST_CONDITIONS
    return event == "card.drawn" and FrozenMap(condition) == _DRAW_CONDITION


def _payer_reference(child):
    return "$context.controller" if child.get("op") in {"draw", "offer_draw"} else "$context.player"


def opponent_event_mana_payment_effect(amount, child):
    cost, _ = mana_cost_to_vector(f"{{{amount}}}")
    spec = FixedEffectPaymentSpec("mana", requirements=cost)
    return {
        "op": "offer_optional_mana_payment", "schema_version": 2, OPPONENT_EVENT_MANA_PAYMENT_FIELD: 1,
        "player": _payer_reference(child), "beneficiary": "$controller", "cost": cost,
        "payment": spec.to_dict(), "effects": [], "declined_effects": [child],
    }


def uses_opponent_source_power_payment(effect):
    value = effect.get(OPPONENT_EVENT_MANA_PAYMENT_FIELD) if isinstance(effect, Mapping) else None
    return type(value) is int and value == 2


def opponent_source_power_payment_condition():
    ordinal = QualifiedSpellOrdinalSpec(PublicHistoryQuerySpec("spell_cast", excluded_types=("creature",)), 1)
    return {
        "all": [
            _CAST_OPPONENT,
            {"not": {"field": "types", "op": "contains_any", "value": ["creature"]}},
            ordinal.to_condition(),
        ],
    }


def _source_power_declined_draw(beneficiary):
    return {"op": "draw", "player": beneficiary, "count": 1, "private": True}


def opponent_source_power_mana_effect():
    return {
        "op": "offer_optional_mana_payment", "schema_version": 2, OPPONENT_EVENT_MANA_PAYMENT_FIELD: 2,
        "player": "$context.controller", "beneficiary": "$controller", "effects": [],
        "source_power": SOURCE_POWER_PRICE.to_dict(), "declined_effects": [_source_power_declined_draw("$controller")],
    }


def _checked_source_power_template(effect):
    if (not uses_opponent_source_power_payment(effect) or type(effect.get("schema_version")) is not int
            or FrozenMap(effect) != FrozenMap(opponent_source_power_mana_effect())
            or type(effect["declined_effects"][0]["count"]) is not int
            or effect["declined_effects"][0]["private"] is not True
            or ScalarEffectAmountSpec.from_dict(effect["source_power"]) != SOURCE_POWER_PRICE):
        raise ValueError("A source-power payment requires its one original unpaid draw")
    return (
        OPPONENT_SOURCE_POWER_MANA_CAPABILITY, OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY,
        FIXED_EFFECT_PAYMENT_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, QUALIFIED_SPELL_ORDINAL_CAPABILITY,
        "zone.draw.library_to_hand",
    )


def materialize_opponent_source_power_payment(host, effect, item):
    from ..scalar_effect_amounts import resolve_scalar_effect_amount

    _checked_source_power_template(effect)
    beneficiary = item.controller
    payer = item.context.get("controller")
    if (item.kind != "triggered_ability" or item.context.get("event") != "spell.cast"
            or type(payer) is not str or not payer or beneficiary == payer):
        raise ValueError("A source-power payment requires the original opposing spell caster")
    amount = resolve_scalar_effect_amount(host, effect["source_power"], item, use_phased_source_lki=True)
    cost, _ = mana_cost_to_vector(f"{{{amount}}}")
    spec = FixedEffectPaymentSpec(
        "mana", requirements=cost, schema_version=OPPONENT_SOURCE_POWER_MANA_PRICE_VERSION,
    )
    return {
        "op": effect["op"], "schema_version": 2, OPPONENT_EVENT_MANA_PAYMENT_FIELD: 2,
        "player": payer, "beneficiary": beneficiary, "effects": [], "cost": cost, "payment": spec.to_dict(),
        "declined_effects": [_source_power_declined_draw(beneficiary)],
    }


def _unpaid_capabilities(child, beneficiary):
    if FrozenMap(child) == FrozenMap({
        "op": "offer_draw", "player": beneficiary, "drawer": beneficiary, "count": 1, "private": True,
    }) and type(child["count"]) is int and child["private"] is True:
        return ("zone.draw.library_to_hand",)
    if (child.get("op") != "create_token" or child.get("controller") != beneficiary
            or child.get("name") != "Treasure Token" or type(child.get("quantity")) is not int
            or child["quantity"] != 1 or child.get("tapped") is not None):
        raise ValueError("An unpaid opponent payment requires the exact optional draw or ordinary Treasure")
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities

    token = {**child, "controller": "$controller"}
    capabilities = fixed_token_creation_node_capabilities(
        effects=(token,), target_schema=None, mechanic_ids={"cr-111-tokens"},
    )
    if not capabilities:
        raise ValueError("An unpaid opponent payment requires the complete typed Treasure definition")
    return capabilities


def _payment_parts(effect, *, actor=None, beneficiary="$controller", continuation=False):
    fields = {"op", "schema_version", OPPONENT_EVENT_MANA_PAYMENT_FIELD, "player", "beneficiary",
              "cost", "payment", "effects", "declined_effects"}
    if continuation:
        fields |= _LOCALS
    source_power = uses_opponent_source_power_payment(effect)
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != "offer_optional_mana_payment" or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 2 or type(effect[OPPONENT_EVENT_MANA_PAYMENT_FIELD]) is not int
            or effect[OPPONENT_EVENT_MANA_PAYMENT_FIELD] != (2 if source_power else 1)
            or effect["beneficiary"] != beneficiary
            or effect["effects"] not in ([], ())):
        raise ValueError("Opponent mana payment has an unsupported result or actor shape")
    if continuation and (effect["_choice_actor"] != actor or effect["_legal_objects"] not in ([], ())
                         or type(effect["_stack_label"]) is not str or not effect["_stack_label"]):
        raise ValueError("Opponent mana payment lost its sealed choice state")
    cost = effect["cost"]
    if not isinstance(cost, Mapping) or type(cost.get("GENERIC")) is not int or cost["GENERIC"] < 0:
        raise ValueError("Opponent mana payment requires one nonnegative generic price")
    if not source_power and not 1 <= cost["GENERIC"] <= 10:
        raise ValueError("Opponent mana payment requires one positive fixed generic price")
    expected_cost, _ = mana_cost_to_vector(f'{{{cost["GENERIC"]}}}')
    if dict(cost) != expected_cost or any(type(value) is not int for value in cost.values()):
        raise ValueError("Opponent mana payment must use the ordinary generic mana vector")
    spec = FixedEffectPaymentSpec(
        "mana", requirements=expected_cost, schema_version=OPPONENT_SOURCE_POWER_MANA_PRICE_VERSION if source_power else 2,
    )
    if not isinstance(effect["payment"], Mapping) or FrozenMap(effect["payment"]) != FrozenMap(spec.to_dict()):
        raise ValueError("Opponent mana payment must keep its original typed price")
    declined = effect["declined_effects"]
    if not isinstance(declined, (list, tuple)) or len(declined) != 1 or not isinstance(declined[0], Mapping):
        raise ValueError("Opponent mana payment requires one complete unpaid result")
    child, = declined
    if effect["player"] != (actor if actor is not None else _payer_reference(child)):
        raise ValueError("Opponent mana payment lost its actual payer")
    if source_power:
        if FrozenMap(child) != FrozenMap(_source_power_declined_draw(beneficiary)) or type(child.get("count")) is not int:
            raise ValueError("Declining a source-power price requires the mandatory controller draw")
        caps = ("zone.draw.library_to_hand",)
    else:
        caps = _unpaid_capabilities(child, beneficiary)
    return spec, (FrozenMap(child),), caps


def opponent_event_mana_payment_capabilities(effect):
    if uses_opponent_source_power_payment(effect):
        return _checked_source_power_template(effect)
    spec, _, caps = _payment_parts(effect)
    return (OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY, *spec.capabilities, *caps)


def opponent_event_mana_payment_program_dependencies(program, effect):
    if uses_opponent_source_power_payment(effect):
        if (program.active_zone != "battlefield" or program.target_schema is not None or program.handlers
                or program.cost_schema is not None or tuple(program.effects) != (effect,)
                or program.event != "spell.cast"
                or FrozenMap(program.event_condition) != FrozenMap(opponent_source_power_payment_condition())):
            raise ValueError("A source-power payment needs the original first opposing noncreature cast")
        return _checked_source_power_template(effect)
    _, children, _ = _payment_parts(effect)
    if (program.active_zone != "battlefield" or program.target_schema is not None
            or program.handlers or program.cost_schema is not None or tuple(program.effects) != (effect,)
            or not opponent_event_payment_conditions_match(program.event, program.event_condition, children[0])):
        raise ValueError("Opponent mana payment needs its original untargeted cast or draw occurrence")
    return opponent_event_mana_payment_capabilities(effect)


def resolved_opponent_event_mana_payment(effect, actor, beneficiary, *, continuation=False):
    if any(type(seat) is not str or not seat or seat.startswith("$") for seat in (actor, beneficiary)):
        raise ValueError("Opponent mana payment requires two resolved player roles")
    spec, children, _ = _payment_parts(effect, actor=actor, beneficiary=beneficiary, continuation=continuation)
    return spec, children


def bind_event_player_payment_choice(item, effect, *, resuming_actor=None):
    from .entry_player_counter_payment import bind_entry_player_counter_choice

    bind_entry_player_counter_choice(item, effect, resuming_actor=resuming_actor)
    if not uses_opponent_event_mana_payment(effect):
        return
    event = item.context.get("event")
    payer = item.context.get("controller" if event == "spell.cast" else "player")
    _, children = resolved_opponent_event_mana_payment(
        effect, payer, item.controller, continuation=resuming_actor is not None,
    )
    expected = "spell.cast" if children[0]["op"] in {"draw", "offer_draw"} else "card.drawn"
    if (item.kind != "triggered_ability" or event != expected
            or resuming_actor is not None and resuming_actor != payer):
        raise ValueError("Only the original opposing caster or drawer may decide whether to pay")
