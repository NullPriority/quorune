from __future__ import annotations

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .graveyard_arrival_order import GRAVEYARD_ARRIVAL_ORDER_CAPABILITY


FIELD = "all_colored_permanent_sacrifice"
CAPABILITY = "zone.sacrifice.fixed_all_colored_permanents"
DEPENDENCIES = (
    CAPABILITY, "choice.affected_player.fixed_sacrifice", GRAVEYARD_ARRIVAL_ORDER_CAPABILITY,
    "trigger.placement.apnap", "zone.change.destination_replacement",
)


def all_colored_sacrifice_effect():
    return {
        "op": "choose_cards_apnap", FIELD: 1, "zone": "battlefield", "then": "sacrifice", "players": "all",
        "predicate": ObjectQuerySpec(zones=("battlefield",), colors_any=tuple("WUBRG")).to_dict(),
    }


def uses_all_colored_sacrifice(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_cards_apnap" and FIELD in effect


def all_colored_sacrifice_is_closed(effect):
    return uses_all_colored_sacrifice(effect) and type(effect[FIELD]) is int and thaw_value(effect) == all_colored_sacrifice_effect()


def all_colored_sacrifice_payload_dependencies(effect):
    if not uses_all_colored_sacrifice(effect):
        return ()
    if not all_colored_sacrifice_is_closed(effect):
        raise ValueError("Malformed all-colored permanent sacrifice descriptor")
    return DEPENDENCIES


def all_colored_sacrifice_program_dependencies(program):
    if not any(uses_all_colored_sacrifice(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if (
        (program.event, program.active_zone) != ("resolve", "stack") or program.target_schema is not None
        or program.cost_schema is not None or program.event_condition is not None or program.handlers
        or len(program.effects) != 1 or not all_colored_sacrifice_is_closed(program.effects[0])
    ):
        raise ValueError("All colored permanents must use one complete untargeted sacrifice spell")
    return DEPENDENCIES
