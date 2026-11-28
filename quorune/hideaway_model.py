"""Closed Hideaway library selection and source-incarnation vocabulary."""

from collections.abc import Mapping

from .replacement.immutable import thaw_value


HIDEAWAY_SELECTION_VERSION = 35
HIDEAWAY_DESTINATION = "exile_face_down_hideaway"
HIDEAWAY_CAPABILITY = "trigger.keyword.hideaway"
HIDEAWAY_MECHANIC = "hideaway"
HIDEAWAY_PAIR = "hideaway:private-library-selection"
HIDEAWAY_SOURCE_ANNOTATION = "hideaway_linked_exile_source"
HIDEAWAY_LOOK_COUNT = 4
HIDEAWAY_PLAY_OPERATION = "hideaway_linked_play"
HIDEAWAY_PLAY_MECHANIC = "linked-hideaway-power-play"
HIDEAWAY_PLAY_CAPABILITY = "zone.exile.linked_hideaway_play"
HIDEAWAY_PLAY_TEMPLATE = "hideaway-linked-power-play-v1"
HIDEAWAY_REQUIRED_POWER = 10


def hideaway_selection_effect(player="$controller"):
    return {
        "op": "fixed_library_selection",
        "schema_version": HIDEAWAY_SELECTION_VERSION,
        "player": player,
        "look_count": HIDEAWAY_LOOK_COUNT,
        "public_reveal": False,
        "selected_reveal": False,
        "selection_policy": "fixed_any",
        "minimum": 1,
        "maximum": 1,
        "predicate_groups": [],
        "remainder_destination": "library_bottom",
        "remainder_order": "random",
        "selected_destination": HIDEAWAY_DESTINATION,
    }


def uses_hideaway_selection(value):
    return (isinstance(value, Mapping) and value.get("op") == "fixed_library_selection"
            and (value.get("schema_version") == HIDEAWAY_SELECTION_VERSION
                 or value.get("selected_destination") == HIDEAWAY_DESTINATION))


def validate_hideaway_selection(value, *, player):
    if (not uses_hideaway_selection(value) or type(player) is not str or not player
            or any(type(value.get(field)) is not int
                   for field in ("schema_version", "look_count", "minimum", "maximum"))
            or value.get("public_reveal") is not False or value.get("selected_reveal") is not False
            or thaw_value(value) != hideaway_selection_effect(player)):
        raise ValueError("Hideaway requires privately exiling one of the top four and randomizing the remainder")


def validate_hideaway_arrangement(arrangement):
    if (arrangement.remainder_destination != "library_bottom" or arrangement.remainder_order != "random"
            or len(arrangement.looked) > HIDEAWAY_LOOK_COUNT
            or len(arrangement.selected_refs) != int(bool(arrangement.looked))):
        raise ValueError("Hideaway must exile one looked-at card if possible and randomly bottom the rest")


def has_current_hideaway_source(permanent):
    return permanent.annotations.get(HIDEAWAY_SOURCE_ANNOTATION) == permanent.logical_object_id


def hideaway_play_effect(player="$controller"):
    return {
        "op": HIDEAWAY_PLAY_OPERATION, "schema_version": 1,
        "player": player, "minimum_power": HIDEAWAY_REQUIRED_POWER,
    }


def validate_hideaway_play_effect(value, *, player):
    if (type(player) is not str or not player or not isinstance(value, Mapping)
            or type(value.get("schema_version")) is not int or type(value.get("minimum_power")) is not int
            or thaw_value(value) != hideaway_play_effect(player)):
        raise ValueError("Hideaway play requires its exact controller and ten current creature power")
