"""Closed timing field for represented controller-relative resolution effects."""

from collections.abc import Mapping
from typing import Any


NEXT_TURN_EFFECT_FIELD = "until_controller_next_turn"
NEXT_TURN_EFFECT_CAPABILITY = "continuous.resolution.until_controller_next_turn"


def supports_next_turn_effect(effect: Mapping[str, Any]) -> bool:
    operation = effect.get("op")
    if operation == "apply_source_characteristics_until_end_of_turn":
        version = effect.get("schema_version")
        return type(version) is int and version in {2, 6, 11} and "announced_target_count" not in effect
    if operation == "modify_all_matching_permanents_until_end_of_turn":
        return (isinstance(effect.get("predicate"), Mapping)
                and type(effect.get("power")) is int and type(effect.get("toughness")) is int)
    if operation == "grant_declaration_restriction_until_end_of_turn":
        return type(effect.get("restriction")) is str
    return False


def split_next_turn_effect(effect: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    """Pass the unchanged result to its original strict shape validator."""
    if NEXT_TURN_EFFECT_FIELD not in effect:
        return effect, False
    if effect[NEXT_TURN_EFFECT_FIELD] is not True or not supports_next_turn_effect(effect):
        raise ValueError("Next-turn timing requires a supported fixed result and its exact timing field")
    return {key: value for key, value in effect.items() if key != NEXT_TURN_EFFECT_FIELD}, True


def next_turn_effect_dependencies(value: Any) -> tuple[str, ...]:
    """Require the timing capability even inside existing effect compositions."""
    return (NEXT_TURN_EFFECT_CAPABILITY,) if _has_next_turn_effect(value) else ()


def _has_next_turn_effect(value: Any) -> bool:
    if isinstance(value, Mapping):
        _, next_turn = split_next_turn_effect(value)
        children = value.values()
    elif isinstance(value, (list, tuple)):
        next_turn = False
        children = value
    else:
        return False
    # Inspect every sibling so a valid earlier effect cannot mask an invalid marker.
    for child in children:
        if _has_next_turn_effect(child):
            next_turn = True
    return next_turn
