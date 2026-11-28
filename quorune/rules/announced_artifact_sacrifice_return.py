"""Closed artifact-surrender activation and its announced creature-power bound."""

from __future__ import annotations

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from .announced_graveyard_copy import owned_graveyard_creature_target_schema


ARTIFACT_SACRIFICE_X_CAPABILITY = "activation.selected_zone_change.announced_artifact_count"
ARTIFACT_SACRIFICE_RETURN_TEMPLATE = "announced-artifact-sacrifice-own-graveyard-return-v1"
ARTIFACT_SACRIFICE_COST_TEXT = "{2}{B}, Sacrifice X artifacts"
ARTIFACT_SACRIFICE_RETURN_TEXT = (
    "Return target creature card with power X or less from your graveyard to the battlefield. X can't be 0."
)
_REQUIREMENTS = {"GENERIC": 2, **dict.fromkeys("WUBRGC", 0), "B": 1}


def announced_artifact_predicate():
    return ObjectQuerySpec(zones=("battlefield",), controller="$actor", types_all=("artifact",), known_to_actor=True)


def has_announced_artifact_sacrifice(ability):
    return any(getattr(choice, "count_parameter", None) == "x" for choice in getattr(ability, "choices", ()))


def announced_artifact_return_effect():
    return {"op": "reanimate", "card": "$target.0", "controller": "$controller", "tapped": False}


def announced_power_return_schema(value):
    if type(value) is not int or value < 1:
        raise ValueError("The artifact surrender cost requires a positive announced X")
    predicate = TargetNumericCharacteristicSpec(
        TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_MOST, value,
    )
    return {**owned_graveyard_creature_target_schema(), "numeric_characteristic": predicate.to_dict()}


def _expected_compact_choice():
    return {
        "k": "sacrifice_one", "n": 1, "z": "battlefield",
        "q": announced_artifact_predicate().to_dict(), "count_parameter": "x",
    }


def _expected_cost():
    return {
        "text": ARTIFACT_SACRIFICE_COST_TEXT, "mana": dict(_REQUIREMENTS),
        "complex_symbols": [], "tap_source": False, "untap_source": False, "discard_source": False,
        "sacrifice_source": False, "exile_source": False, "life_payment": 0, "energy_payment": 0,
        "loyalty_delta": None, "source_counter_removal_cost": None,
        "choices": [_expected_compact_choice()], "uncompiled_costs": [],
    }


def has_announced_artifact_sacrifice_program(program):
    price = getattr(program, "cost_schema", None)
    choices = price.get("choices") if isinstance(price, Mapping) else None
    return isinstance(choices, (tuple, list)) and any(
        isinstance(choice, Mapping) and "count_parameter" in choice for choice in choices
    )


def announced_artifact_sacrifice_program_dependencies(program):
    if not has_announced_artifact_sacrifice_program(program):
        return ()
    price = thaw_value(program.cost_schema)
    expected = _expected_cost()
    if (
        program.event != "activate" or program.active_zone != "battlefield"
        or price != expected or any(type(price[k]) is not type(v) for k, v in expected.items())
        or any(type(value) is not int for value in price["mana"].values())
        or type(price["choices"][0]["n"]) is not int
        or thaw_value(program.target_schema) != owned_graveyard_creature_target_schema()
        or thaw_value(program.effects) != [announced_artifact_return_effect()]
    ):
        raise ValueError("Announced artifact surrender requires its complete fixed-mana power-bounded return")
    return (ARTIFACT_SACRIFICE_X_CAPABILITY, "activation.selected_zone_change.fixed",
            "target.revalidate_resolution", "zone.reanimate.fixed_target")


def validate_announced_artifact_sacrifice_ability(ability):
    if not has_announced_artifact_sacrifice(ability):
        return
    if (
        ability.cost_text != ARTIFACT_SACRIFICE_COST_TEXT or ability.effect_text != ARTIFACT_SACRIFICE_RETURN_TEXT
        or ability.zones != ("battlefield",) or ability.sorcery_speed or ability.activation_conditions
        or ability.mana_ability or dict(ability.mana) != _REQUIREMENTS
        or tuple(choice.compact() for choice in ability.choices) != (_expected_compact_choice(),)
        or any((ability.announced_x_symbols is not None, ability.loyalty_x, ability.variable_colored_reveal_price,
                ability.sacrifice_source, ability.tap_source, ability.untap_source, ability.exile_source,
                ability.discard_source, ability.life_payment, ability.energy_payment, ability.uncompiled_costs,
                ability.mana_cost_options, ability.source_counter_removal_cost, ability.activation_limit,
                ability.complex_symbols, ability.waterbend, ability.loyalty_delta is not None))
    ):
        raise ValueError("Announced artifact surrender requires its exact battlefield price and return result")
