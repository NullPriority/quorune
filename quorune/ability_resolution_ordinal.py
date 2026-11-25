from __future__ import annotations

"""Closed ability-resolution ordinal descriptors and history event kind."""

from collections.abc import Mapping

from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value


ABILITY_RESOLVED_HISTORY = "ability_resolved"
SECOND_ABILITY_DRAW_VERSION = 20
SECOND_ABILITY_DRAW_CONDITION = {"kind": "ability_resolution_ordinal", "schema_version": 1, "ordinal": 2}
THIRD_ABILITY_RESULT_VERSION = 21
THIRD_ABILITY_RESULT_CONDITION = {"kind": "ability_resolution_ordinal", "schema_version": 1, "ordinal": 3}
_FIELDS = frozenset({
    "op", "schema_version", "player", "condition", "resolution_ordinal",
    "effects", "mechanic_ids", "prefix_mechanic_ids",
})


def is_second_ability_draw(effect) -> bool:
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == SECOND_ABILITY_DRAW_VERSION
    )


def validate_second_ability_draw(effect, *, bound=False):
    if (not is_second_ability_draw(effect) or set(effect) != _FIELDS or type(effect["schema_version"]) is not int):
        raise ValueError("The ability-resolution draw requires its exact instruction version")
    condition = effect["condition"]
    if (not isinstance(condition, Mapping)
            or dict(condition) != SECOND_ABILITY_DRAW_CONDITION
            or type(condition["schema_version"]) is not int or type(condition["ordinal"]) is not int):
        raise ValueError("The ability-resolution draw requires the exact second occurrence")
    actor, observed = effect["player"], effect["resolution_ordinal"]
    if bound:
        if type(actor) is not str or not actor or actor.startswith("$") or type(observed) is not int or observed < 1:
            raise ValueError("The ability-resolution draw is missing its committed ordinal and controller")
    elif actor != "$controller" or observed is not None:
        raise ValueError("The ability-resolution draw must obtain its ordinal from the stack owner")
    results = effect["effects"]
    expected = {"op": "draw", "player": actor, "count": 1, "private": True}
    if (not isinstance(results, (list, tuple)) or len(results) != 1 or not isinstance(results[0], Mapping)
            or thaw_value(FrozenMap(results[0])) != expected or type(results[0]["count"]) is not int
            or results[0]["private"] is not True):
        raise ValueError("The second ability resolution permits one private controller draw")
    for field in ("mechanic_ids", "prefix_mechanic_ids"):
        names = effect[field]
        if (not isinstance(names, (list, tuple)) or not names
                or any(type(name) is not str or not name for name in names) or len(set(names)) != len(names)):
            raise ValueError("The ability-resolution draw requires its complete fixed component mechanics")
    return tuple(results)

def is_third_ability_result(effect) -> bool:
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == THIRD_ABILITY_RESULT_VERSION
    )


def is_ability_resolution_ordinal(effect) -> bool:
    return is_second_ability_draw(effect) or is_third_ability_result(effect)


def ordinal_controller_creatures(controller):
    return ObjectQuerySpec(
        zones=("battlefield",), controller=controller, types_all=("creature",),
    ).to_dict()


def _fixed_third_resolution_mana(effect, player, count, *, bound):
    if not isinstance(effect, Mapping):
        return False
    source = effect.get("source")
    if bound:
        if type(source) is not str or not source or source.startswith("$"):
            return False
    elif source != "$source":
        return False
    return type(effect.get("amount")) is int and dict(effect) == {
        "op": "mana", "player": player, "color": "R", "amount": count, "source": source,
    }


def _is_third_resolution_child(effect, player, *, bound):
    if not isinstance(effect, Mapping):
        return False
    if effect.get("op") == "mana":
        return _fixed_third_resolution_mana(effect, player, 4, bound=bound)
    if effect.get("op") == "offer_optional_effect":
        children = effect.get("effects")
        return (
            set(effect) == {"op", "player", "effects"} and effect["player"] == player
            and isinstance(children, (list, tuple)) and len(children) == 1
            and _fixed_third_resolution_mana(children[0], player, 8, bound=bound)
        )
    return (
        type(effect.get("power")) is int and type(effect.get("toughness")) is int
        and thaw_value(FrozenMap(effect)) == {
            "op": "modify_all_matching_permanents_until_end_of_turn",
            "predicate": ordinal_controller_creatures(player),
            "power": 0, "toughness": 0, "keywords": ["First Strike"],
        }
    )


def validate_third_ability_result(effect, *, bound=False):
    if (not is_third_ability_result(effect) or set(effect) != _FIELDS
            or type(effect["schema_version"]) is not int):
        raise ValueError("The third-resolution result requires its exact instruction version")
    condition = effect["condition"]
    if (not isinstance(condition, Mapping)
            or dict(condition) != THIRD_ABILITY_RESULT_CONDITION
            or type(condition["schema_version"]) is not int or type(condition["ordinal"]) is not int):
        raise ValueError("The third-resolution result requires the exact third occurrence")
    actor, observed = effect["player"], effect["resolution_ordinal"]
    if bound:
        if type(actor) is not str or not actor or actor.startswith("$") or type(observed) is not int or observed < 1:
            raise ValueError("The third-resolution result is missing its committed ordinal and controller")
    elif actor != "$controller" or observed is not None:
        raise ValueError("The third-resolution result must obtain its ordinal from the stack owner")
    results = effect["effects"]
    if (not isinstance(results, (list, tuple)) or len(results) != 1
            or not _is_third_resolution_child(results[0], actor, bound=bound)):
        raise ValueError("The third resolution requires its exact fixed mana or first-strike result")
    for field in ("mechanic_ids", "prefix_mechanic_ids"):
        names = effect[field]
        if (not isinstance(names, (list, tuple)) or not names
                or any(type(name) is not str or not name for name in names) or len(set(names)) != len(names)):
            raise ValueError("The third-resolution result requires its complete fixed component mechanics")
    return tuple(results)


def validate_ability_resolution_ordinal(effect, *, bound=False):
    if is_second_ability_draw(effect):
        return validate_second_ability_draw(effect, bound=bound)
    return validate_third_ability_result(effect, bound=bound)
