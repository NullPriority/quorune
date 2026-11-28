from __future__ import annotations

"""Closed ability-resolution ordinal descriptors and history event kind."""

from collections.abc import Mapping

from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value


ABILITY_RESOLVED_HISTORY = "ability_resolved"
FIRST_ABILITY_COUNTER_VERSION = 36
FIRST_ABILITY_COUNTER_CONDITION = {"kind": "ability_resolution_ordinal", "schema_version": 1, "ordinal": 1}
FIRST_ARTIFACT_ENTRY_COUNTER_CAPABILITY = "resolution.effect.first_artifact_entry_source_counter"
SECOND_ABILITY_DRAW_VERSION = 20
SECOND_ABILITY_DRAW_CONDITION = {"kind": "ability_resolution_ordinal", "schema_version": 1, "ordinal": 2}
THIRD_ABILITY_RESULT_VERSION = 21
THIRD_ABILITY_RESULT_CONDITION = {"kind": "ability_resolution_ordinal", "schema_version": 1, "ordinal": 3}
SECOND_ABILITY_REVEAL_VERSION = 31
SECOND_ABILITY_SCRY_REPLACEMENT_VERSION = 42
SECOND_LAND_ENTRY_REVEAL_CAPABILITY = "resolution.effect.second_land_entry_reveal_until_subtype"
_FIELDS = frozenset({
    "op", "schema_version", "player", "condition", "resolution_ordinal",
    "effects", "mechanic_ids", "prefix_mechanic_ids",
})


def is_first_ability_counter(effect) -> bool:
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == FIRST_ABILITY_COUNTER_VERSION
    )


def is_second_ability_draw(effect) -> bool:
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == SECOND_ABILITY_DRAW_VERSION
    )


def is_second_ability_reveal(effect):
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == SECOND_ABILITY_REVEAL_VERSION
    )


def is_second_ability_scry_replacement(effect) -> bool:
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == SECOND_ABILITY_SCRY_REPLACEMENT_VERSION
    )


def _validate_triggered_ordinal_frame(effect, *, expected, condition_spec, ordinal_name, result, bound, fields=_FIELDS):
    if (not expected or set(effect) != fields or type(effect["schema_version"]) is not int):
        raise ValueError(f"The ability-resolution {result} requires its exact instruction version")
    condition = effect["condition"]
    if (not isinstance(condition, Mapping)
            or dict(condition) != condition_spec
            or type(condition["schema_version"]) is not int or type(condition["ordinal"]) is not int):
        raise ValueError(f"The ability-resolution {result} requires the exact {ordinal_name} occurrence")
    actor, observed = effect["player"], effect["resolution_ordinal"]
    if bound:
        if type(actor) is not str or not actor or actor.startswith("$") or type(observed) is not int or observed < 1:
            raise ValueError(f"The ability-resolution {result} is missing its committed ordinal and controller")
    elif actor != "$controller" or observed is not None:
        raise ValueError(f"The ability-resolution {result} must obtain its ordinal from the stack owner")
    return actor


def validate_first_ability_counter(effect, *, bound=False):
    _validate_triggered_ordinal_frame(
        effect, expected=is_first_ability_counter(effect), condition_spec=FIRST_ABILITY_COUNTER_CONDITION,
        ordinal_name="first", result="counter", bound=bound,
    )
    results = effect["effects"]
    if (not isinstance(results, (list, tuple)) or len(results) != 1
            or not isinstance(results[0], Mapping)
            or not isinstance(effect["mechanic_ids"], (list, tuple))
            or tuple(effect["mechanic_ids"]) != ("cr-122-counters",)
            or not isinstance(effect["prefix_mechanic_ids"], (list, tuple))
            or tuple(effect["prefix_mechanic_ids"]) != ("cr-119-life",)):
        raise ValueError("The first ability resolution requires one fixed life and counter instruction")
    child = results[0]
    card, source = child.get("card"), child.get("source")
    if bound:
        has_subject = type(source) is str and bool(source) and not source.startswith("$") and card in (None, source)
    else:
        has_subject = (card, source) == ("$source.zone_object", "$source")
    if (not has_subject or type(child.get("amount")) is not int or dict(child) != {
        "op": "place_counters", "card": card, "counter": "+1/+1", "amount": 1, "source": source,
    }):
        raise ValueError("The first ability resolution places one counter on the original source")
    return tuple(results)


def validate_second_ability_draw(effect, *, bound=False):
    actor = _validate_triggered_ordinal_frame(
        effect, expected=is_second_ability_draw(effect), condition_spec=SECOND_ABILITY_DRAW_CONDITION,
        ordinal_name="second", result="draw", bound=bound,
    )
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

def second_ability_scry_replacement():
    return {
        "op": "apply_if_public_condition", "schema_version": SECOND_ABILITY_SCRY_REPLACEMENT_VERSION,
        "player": "$controller", "condition": dict(SECOND_ABILITY_DRAW_CONDITION), "resolution_ordinal": None,
        "effects": [{"op": "draw", "player": "$controller", "count": 1, "private": True}],
        "otherwise_effects": [{"op": "scry", "player": "$controller", "count": 1}],
        "mechanic_ids": ["cr-121-drawing-a-card"], "otherwise_mechanic_ids": ["scry"], "prefix_mechanic_ids": [],
    }


def validate_second_ability_scry_replacement(effect, *, bound=False):
    alternate_fields = _FIELDS | {"otherwise_effects", "otherwise_mechanic_ids"}
    actor = _validate_triggered_ordinal_frame(
        effect, expected=is_second_ability_scry_replacement(effect), condition_spec=SECOND_ABILITY_DRAW_CONDITION,
        ordinal_name="second", result="scry replacement", bound=bound, fields=alternate_fields,
    )
    expected = second_ability_scry_replacement()
    for field in ("mechanic_ids", "otherwise_mechanic_ids", "prefix_mechanic_ids"):
        mechanics = effect[field]
        if not isinstance(mechanics, (tuple, list)) or tuple(mechanics) != tuple(expected[field]):
            raise ValueError("The second-resolution scry replacement requires both exact single-card instructions")
    for field in ("effects", "otherwise_effects"):
        children = effect[field]
        child = {**expected[field][0], "player": actor}
        if (not isinstance(children, (tuple, list)) or len(children) != 1 or not isinstance(children[0], Mapping)
                or dict(children[0]) != child or type(children[0]["count"]) is not int):
            raise ValueError("The second-resolution scry replacement requires both exact single-card instructions")
    if effect["effects"][0]["private"] is not True:
        raise ValueError("The second-resolution draw is private")
    return tuple(effect["effects"])


def validate_second_ability_reveal(effect, *, bound=False):
    actor = _validate_triggered_ordinal_frame(
        effect, expected=is_second_ability_reveal(effect), condition_spec=SECOND_ABILITY_DRAW_CONDITION,
        ordinal_name="second", result="reveal", bound=bound,
    )
    results = effect["effects"]
    if (not isinstance(results, (list, tuple)) or len(results) != 1
            or not isinstance(effect["mechanic_ids"], (list, tuple))
            or tuple(effect["mechanic_ids"]) != ("reveal-until-library-selection",)
            or not isinstance(effect["prefix_mechanic_ids"], (list, tuple))
            or tuple(effect["prefix_mechanic_ids"]) != ("fixed-resolution-mana",)):
        raise ValueError("The second land entry resolution requires one exact mana choice and reveal result")
    predicates = [ObjectQuerySpec(zones=("library",), subtypes_any=(subtype,)).to_dict()
                  for subtype in ("elf", "elemental")]
    expected = {
        "op": "reveal_until_library_selection", "schema_version": 1, "player": actor, "stop_count": 1,
        "predicate_groups": [predicates], "selection": "matching", "selected_destination": "hand",
        "entry_tapped": False, "remainder_destination": "library_bottom", "remainder_order": "random",
    }
    child = results[0]
    if (not isinstance(child, Mapping) or thaw_value(FrozenMap(child)) != expected
            or type(child["schema_version"]) is not int or type(child["stop_count"]) is not int
            or child["entry_tapped"] is not False):
        raise ValueError("The second land entry resolution reveals through one Elf or Elemental, then random-bottoms the rest")
    return tuple(results)


def is_third_ability_result(effect) -> bool:
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == THIRD_ABILITY_RESULT_VERSION
    )


def is_triggered_ability_resolution_ordinal(effect) -> bool:
    return (
        is_first_ability_counter(effect) or is_second_ability_draw(effect) or is_second_ability_reveal(effect)
        or is_second_ability_scry_replacement(effect)
    )


def is_ability_resolution_ordinal(effect) -> bool:
    return is_triggered_ability_resolution_ordinal(effect) or is_third_ability_result(effect)


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
    if is_first_ability_counter(effect):
        return validate_first_ability_counter(effect, bound=bound)
    if is_second_ability_draw(effect):
        return validate_second_ability_draw(effect, bound=bound)
    if is_second_ability_reveal(effect):
        return validate_second_ability_reveal(effect, bound=bound)
    if is_second_ability_scry_replacement(effect):
        return validate_second_ability_scry_replacement(effect, bound=bound)
    return validate_third_ability_result(effect, bound=bound)
