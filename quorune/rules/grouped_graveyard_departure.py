"""Validate one group trigger that creates a token before distributing counters."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..counter_placement_sets import controlled_counter_subtype_subjects
from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap, thaw_value
from .counter_placement_capability_shapes import fixed_counter_placement_set_node_capabilities
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities

COVERAGE = "grouped_graveyard_departure_token_then_counters"
VARIANT = "one_or_more_controller_creature_cards_leave_graveyard"
TOKEN_MECHANIC, COUNTER_MECHANIC = ("cr-111-tokens", "cr-122-counters")


def grouped_graveyard_departure_condition() -> dict:
    return {"all": [
        {"field": "owner", "op": "eq", "value": "$source.controller"},
        {"field": "types", "op": "contains_any", "value": ["creature"]},
    ]}


def token_then_subtype_counter_dependencies(effects, *, target_schema=None):
    """Recognize two ordered, untargeted operations on one fixed subtype."""
    if target_schema is not None or len(effects) != 2:
        return ()
    token, counter = effects
    if not isinstance(token, Mapping) or not isinstance(counter, Mapping):
        return ()
    characteristics = token.get("characteristics")
    if not isinstance(characteristics, Mapping) or set(characteristics) != {
        "type_line", "literal_token_name_version", "colors", "power", "toughness",
    }:
        return ()
    type_line = characteristics.get("type_line")
    if type(type_line) is not str or not type_line.startswith("Token Creature — "):
        return ()
    subtype = canonical_creature_subtype(type_line.removeprefix("Token Creature — "))
    if subtype is None or type(token.get("quantity")) is not int or token["quantity"] != 1:
        return ()
    if token.get("controller") != "$controller":
        return ()
    if type(counter.get("amount")) is not int or counter.get("counter") != "+1/+1":
        return ()
    affected = counter.get("set")
    if not isinstance(affected, Mapping) or FrozenMap(affected) != FrozenMap(controlled_counter_subtype_subjects(subtype).to_dict()):
        return ()
    token_caps = fixed_token_creation_node_capabilities(
        effects=(thaw_value(token),), target_schema=None, mechanic_ids={TOKEN_MECHANIC},
    )
    counter_caps = fixed_counter_placement_set_node_capabilities(
        effects=(thaw_value(counter),), target_schema=None, mechanic_ids=(COUNTER_MECHANIC,),
    )
    if not token_caps or not counter_caps:
        return ()
    return (*token_caps, *counter_caps)


def grouped_graveyard_departure_program_dependencies(program, *, event):
    required = token_then_subtype_counter_dependencies(program.effects, target_schema=program.target_schema)
    if (
        not required or program.active_zone != "battlefield" or program.event != event
        or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
        or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, "one_or_more_event_batch", COVERAGE} <= set(program.coverage)
        or FrozenMap(program.event_condition or {}) != FrozenMap(grouped_graveyard_departure_condition())
    ):
        raise ValueError("Grouped graveyard departures require one closed token and counter sequence")
    return (*required, "trigger.effect.fixed_event", "trigger.event.normalized_zone_change", "trigger.placement.apnap")
