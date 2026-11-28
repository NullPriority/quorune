from __future__ import annotations

"""Parse fixed temporary results with a complete creature-subtype set."""

from ..activation_x import exact_mana_only_x_activation_cost
from ..rules.creature_subtype_set_results import (
    CREATURE_SUBTYPE_SET_RESULT_MECHANIC, CreatureSubtypeSetResult,
    PAID_CONTROLLER_SUBTYPE_SET_TEMPLATE, paid_controller_subtype_set_effect,
)
from . import regex_working_set as re


_ONE = re.compile(r"Target creature (?P<change>gains|loses) all creature types until end of turn\.", re.I)
_REMOVE = re.compile(
    r"Target creature gets (?P<p>[+-]\d+)/(?P<t>[+-]\d+) and loses all creature types until end of turn\.", re.I,
)
_ADD = re.compile(
    r"(?P<subject>Up to two target creatures each|Creatures target player controls) get "
    r"(?P<p>[+-]\d+)/(?P<t>[+-]\d+) and gain all creature types until end of turn\.", re.I,
)
_BASE = re.compile(
    r"Until end of turn, target creature has base power and toughness (?P<p>\d+)/(?P<t>\d+), "
    r"gains all creature types, and gains flying\.", re.I,
)


def _result(text):
    if match := _ONE.fullmatch(text):
        return CreatureSubtypeSetResult("one_creature", "all" if match["change"].casefold() == "gains" else "none")
    if match := _REMOVE.fullmatch(text):
        return CreatureSubtypeSetResult("one_creature", "none", modifier=(int(match["p"]), int(match["t"])))
    if match := _ADD.fullmatch(text):
        scope = "up_to_two_creatures" if match["subject"].casefold().startswith("up") else "target_players_creatures"
        return CreatureSubtypeSetResult(scope, "all", modifier=(int(match["p"]), int(match["t"])))
    if match := _BASE.fullmatch(text):
        return CreatureSubtypeSetResult("one_creature", "all", base=(int(match["p"]), int(match["t"])), flying=True)
    return None


def creature_subtype_set_template(text):
    result = _result(text.strip())
    if result is None:
        return None
    return (
        "temporary-creature-subtype-set-result-v1", (result.to_instruction(),), result.to_target_schema(),
        (CREATURE_SUBTYPE_SET_RESULT_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"),
    )



def paid_controller_subtype_set_template(ability, text):
    from .activated_costs import activated_ability_cost

    if (" ".join(text.casefold().split()) !=
            "until end of turn, creatures you control have base power and toughness x/x and gain all creature types."):
        return None
    if (ability.zones != ("battlefield",) or ability.mana_ability or ability.sorcery_speed
            or ability.activation_conditions or ability.target_schema is not None
            or ability.generic_reduction_per_legendary_creature or ability.library_search_types
            or not exact_mana_only_x_activation_cost(activated_ability_cost(ability))):
        return None
    return (PAID_CONTROLLER_SUBTYPE_SET_TEMPLATE, (paid_controller_subtype_set_effect(),), None,
            (CREATURE_SUBTYPE_SET_RESULT_MECHANIC, "cr-611-continuous-effects"))
