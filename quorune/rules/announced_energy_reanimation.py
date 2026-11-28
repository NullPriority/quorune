from __future__ import annotations

"""Closed printed energy-X price for an owned-graveyard creature return."""

from collections.abc import Mapping

from ..activation_cost_choice import CostChoice
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..util import stable_json


FIELD = "announced_energy_reanimation"
CAPABILITY = "activation.energy.announced_reanimation"
COST_TEXT = "Pay X {E}, Sacrifice a creature, Return this enchantment to its owner's hand"
EFFECT_TEXT = "Return target creature card with mana value X from your graveyard to the battlefield."
_REANIMATION_MANA = dict.fromkeys(("GENERIC", *"WUBRGC"), 0)
ENERGY_REANIMATION_X_EQUAL = {
    "schema_version": 4, "parameter": "x", "comparison": "equal", "scope": "own_graveyard_creature",
}


def energy_reanimation_target_scope() -> dict:
    return {"zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
            "types_any": ["creature"], "count": 1}


def declared_energy_reanimation_target_schema() -> dict:
    return {**energy_reanimation_target_scope(), "announced_mana_value_limit": dict(ENERGY_REANIMATION_X_EQUAL)}


def energy_reanimation_text_matches(cost: str, effect: str) -> bool:
    return cost.strip() == COST_TEXT and " ".join(effect.strip().split()).casefold().removesuffix(
        " activate only as a sorcery."
    ) == EFFECT_TEXT.casefold()


def energy_reanimation_sacrifice_choice() -> CostChoice:
    return CostChoice(
        kind="sacrifice_one", count=1, zone="battlefield", another=True,
        predicate=FrozenMap(ObjectQuerySpec(
            zones=("battlefield",), controller="$actor", types_any=("creature",), known_to_actor=True,
        ).to_dict()),
    )


def validate_energy_reanimation_ability(ability) -> None:
    if type(ability.announced_energy_reanimation) is not bool:
        raise ValueError("The announced energy reanimation marker must be Boolean")
    if not ability.announced_energy_reanimation:
        return
    if (
        not energy_reanimation_text_matches(ability.cost_text, ability.effect_text)
        or ability.zones != ("battlefield",) or ability.sorcery_speed is not True
        or dict(ability.mana) != _REANIMATION_MANA
        or ability.choices != (energy_reanimation_sacrifice_choice(),)
        or thaw_value(ability.target_schema) != declared_energy_reanimation_target_schema()
        or any((
            ability.mana_ability, ability.tap_source, ability.untap_source, ability.discard_source,
            ability.sacrifice_source, ability.exile_source, ability.mana_cost_options, ability.waterbend,
            ability.life_payment, ability.energy_payment, ability.random_discard_payment,
            ability.complex_symbols, ability.uncompiled_costs, ability.loyalty_x, ability.activation_conditions,
            ability.activation_limit, ability.ability_local_mana_reduction, ability.generic_reduction_per_legendary_creature,
            ability.granting_attachment_payment, ability.library_search_types, ability.mill_library_cost,
            ability.source_counter_placement_cost, ability.reconfigure, ability.boast, ability.forecast,
        ))
        or any(value is not None for value in (
            ability.loyalty_delta, ability.announced_x_symbols, ability.variable_colored_reveal_price,
            ability.source_counter_removal_cost, ability.crew_threshold, ability.saddle_threshold,
            ability.power_up,
        ))
    ):
        raise ValueError("Announced energy reanimation requires its exact sorcery activation price and target")


def has_energy_reanimation_price(cost) -> bool:
    return isinstance(cost, Mapping) and FIELD in cost


def exact_energy_reanimation_price(cost) -> bool:
    if not has_energy_reanimation_price(cost):
        return False
    expected = {
        "text": COST_TEXT, "mana": _REANIMATION_MANA,
        "complex_symbols": [], "tap_source": False, "untap_source": False, "discard_source": False,
        "sacrifice_source": False, "exile_source": False, "life_payment": 0, "energy_payment": 0,
        "loyalty_delta": None, "source_counter_removal_cost": None,
        "choices": [energy_reanimation_sacrifice_choice().compact()], "uncompiled_costs": [],
        FIELD: True,
    }
    try:
        return stable_json(thaw_value(cost)) == stable_json(expected)
    except (TypeError, ValueError):
        return False


def activation_energy_payment(ability, announced_x) -> int:
    if not ability.announced_energy_reanimation:
        return ability.energy_payment
    if type(announced_x) is not int or announced_x < 0:
        raise ValueError("Energy payment requires one explicit nonnegative X")
    return announced_x
