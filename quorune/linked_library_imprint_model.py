"""Closed linked library exile and the corresponding death results."""

from collections.abc import Mapping

from .continuous_effect_model import ContinuousObjectIdentity
from .linked_exile_return_model import LINKED_RETURN_OPERATION
from .library_search_model import LINKED_ARTIFACT_SEARCH_VERSION, LINKED_SAGA_LIBRARY_SEARCH_VERSION
from .replacement.immutable import thaw_value


LIBRARY_IMPRINT_SELECTION_VERSION = 14
LIBRARY_IMPRINT_DESTINATION = "exile_face_down"
LIBRARY_IMPRINT_PHASE_FIELD = "library_imprint_phase"
LIBRARY_IMPRINT_MECHANIC = "linked-library-imprint"
LIBRARY_IMPRINT_CAPABILITY = "zone.exile.linked_library_face_down"
LIBRARY_IMPRINT_PAIR = "imprint:private-library-selection"
LIBRARY_IMPRINT_PHASES = ("reveal", "return_creatures")


def library_imprint_selection_effect(player="$controller"):
    return {
        "op": "fixed_library_selection",
        "schema_version": LIBRARY_IMPRINT_SELECTION_VERSION,
        "player": player,
        "look_count": 4,
        "public_reveal": False,
        "selected_reveal": False,
        "selection_policy": "fixed_any",
        "minimum": 1,
        "maximum": 1,
        "predicate_groups": [],
        "remainder_destination": "library_bottom",
        "remainder_order": "chosen",
        "selected_destination": LIBRARY_IMPRINT_DESTINATION,
    }


def library_imprint_death_effects():
    return tuple({"op": LINKED_RETURN_OPERATION, "schema_version": 1, LIBRARY_IMPRINT_PHASE_FIELD: phase}
                 for phase in LIBRARY_IMPRINT_PHASES)


def uses_library_imprint_selection(value):
    return (isinstance(value, Mapping) and value.get("op") == "fixed_library_selection"
            and (value.get("schema_version") == LIBRARY_IMPRINT_SELECTION_VERSION
                 or value.get("selected_destination") == LIBRARY_IMPRINT_DESTINATION))


def validate_library_imprint_selection(value, *, player):
    if not isinstance(value, Mapping) or type(player) is not str or not player:
        raise ValueError("Private library imprint requires its original selection player")
    expected = library_imprint_selection_effect(player)
    if set(value) != set(expected) or any(
        type(value[field]) is not int for field in ("schema_version", "look_count", "minimum", "maximum")
    ) or value["public_reveal"] is not False or value["selected_reveal"] is not False:
        raise ValueError("Private library imprint has a closed version and visibility")
    if thaw_value(value) != expected:
        raise ValueError("Private library imprint requires one of the actor's top four and a chosen bottom order")


def uses_library_imprint_death(value):
    return (isinstance(value, Mapping) and value.get("op") == LINKED_RETURN_OPERATION
            and LIBRARY_IMPRINT_PHASE_FIELD in value)


def library_imprint_phase(value):
    if (not uses_library_imprint_death(value) or set(value) != {"op", "schema_version", LIBRARY_IMPRINT_PHASE_FIELD}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value[LIBRARY_IMPRINT_PHASE_FIELD] not in LIBRARY_IMPRINT_PHASES):
        raise ValueError("Linked library imprint has exactly one typed death instruction phase")
    return value[LIBRARY_IMPRINT_PHASE_FIELD]


def library_imprint_source(item, *, actor):
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise ValueError("Linked library imprint requires its original resolving trigger")
    return ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))


LINKED_ARTIFACT_PAIR_FIELD = "linked_library_artifact_pair"
LINKED_ARTIFACT_PAIR_PREFIX = "library-artifact-exile:"


def uses_linked_artifact_library_choice(effect):
    children = effect.get("effects", ()) if isinstance(effect, Mapping) else ()
    return isinstance(children, (list, tuple)) and any(uses_linked_artifact_library_instruction(child) for child in children)


def linked_artifact_library_instruction(pair, *, returning=False):
    if returning:
        return {"op": LINKED_RETURN_OPERATION, "schema_version": 1, LINKED_ARTIFACT_PAIR_FIELD: pair}
    return {
        "op": "search", "schema_version": LINKED_ARTIFACT_SEARCH_VERSION, LINKED_ARTIFACT_PAIR_FIELD: pair,
        "zone": "library", "selector": {"types": ["artifact"]}, "count": {"minimum": 1, "maximum": 1},
        "destination": "exile", "reveal": False, "shuffle_after": True,
    }


def uses_linked_artifact_library_instruction(effect):
    return (isinstance(effect, Mapping) and (LINKED_ARTIFACT_PAIR_FIELD in effect
            or effect.get("op") == "search" and effect.get("schema_version") == LINKED_ARTIFACT_SEARCH_VERSION))


def validate_linked_artifact_library_instruction(effect):
    if not uses_linked_artifact_library_instruction(effect) or type(effect.get("schema_version")) is not int:
        raise ValueError("Linked artifact library instructions need their exact version")
    pair = effect.get(LINKED_ARTIFACT_PAIR_FIELD)
    if (type(pair) is not str or not pair.startswith(LINKED_ARTIFACT_PAIR_PREFIX)
            or len(pair) != len(LINKED_ARTIFACT_PAIR_PREFIX) + 24
            or any(digit not in "0123456789abcdef" for digit in pair[len(LINKED_ARTIFACT_PAIR_PREFIX):])):
        raise ValueError("Linked artifact library instructions need their original ability pair")
    returning = effect.get("op") == LINKED_RETURN_OPERATION
    if thaw_value(effect) != linked_artifact_library_instruction(pair, returning=returning):
        raise ValueError("Linked artifact library instructions need the closed search or owner's hand return")
    if not returning and (type(effect["shuffle_after"]) is not bool or type(effect["reveal"]) is not bool
            or any(type(value) is not int for value in effect["count"].values())):
        raise ValueError("Linked artifact search count and visibility must be exact")
    return pair, returning


def linked_artifact_library_choice(wrapper, *, player):
    if (not isinstance(wrapper, Mapping) or set(wrapper) != {"op", "player", "effects"}
            or wrapper["op"] != "offer_optional_effect" or wrapper["player"] != player
            or not isinstance(wrapper["effects"], (tuple, list)) or len(wrapper["effects"]) != 1):
        raise ValueError("Linked artifact library instructions need one optional controller result")
    child, = wrapper["effects"]
    validate_linked_artifact_library_instruction(child)
    return child


LINKED_SAGA_LIBRARY_CAPABILITY = "zone.exile.linked_saga_library"
LINKED_SAGA_LIBRARY_MECHANIC = "linked-saga-library-exile"
LINKED_SAGA_LIBRARY_PHASE = "linked_saga_library_phase"
LINKED_SAGA_LIBRARY_PAIR = "saga:linked-library-exile"
LINKED_SAGA_LIBRARY_PHASES = ("search", "reveal_and_lose_life", "enter_permanents", "owner_hands")


def linked_saga_library_instruction(phase):
    if phase == "search":
        return {"op": "search", "schema_version": LINKED_SAGA_LIBRARY_SEARCH_VERSION,
                LINKED_SAGA_LIBRARY_PHASE: phase, "zone": "library", "selector": {},
                "count": {"minimum": 1, "maximum": 1}, "destination": "exile",
                "reveal": False, "shuffle_after": True}
    if phase not in LINKED_SAGA_LIBRARY_PHASES:
        raise ValueError("Unknown linked Saga library phase")
    return {"op": LINKED_RETURN_OPERATION, "schema_version": 1, LINKED_SAGA_LIBRARY_PHASE: phase}


def uses_linked_saga_library_instruction(value):
    return isinstance(value, Mapping) and LINKED_SAGA_LIBRARY_PHASE in value


def linked_saga_library_phase(value):
    if not uses_linked_saga_library_instruction(value):
        raise ValueError("Linked Saga library instruction has no bound phase")
    phase = value[LINKED_SAGA_LIBRARY_PHASE]
    if (type(value.get("schema_version")) is not int or thaw_value(value) != linked_saga_library_instruction(phase)
            or phase == "search" and (type(value["reveal"]) is not bool or type(value["shuffle_after"]) is not bool
                                    or any(type(bound) is not int for bound in value["count"].values()))):
        raise ValueError("Linked Saga library instruction does not match its closed phase")
    return phase


def linked_saga_library_chapter_effects(chapter):
    if chapter == 1:
        return (linked_saga_library_instruction("search"),)
    if chapter == 2:
        return (linked_saga_library_instruction("reveal_and_lose_life"),)
    if chapter == 3:
        return (
            {"op": "offer_optional_effect", "player": "$controller",
             "effects": [linked_saga_library_instruction("enter_permanents")]},
            linked_saga_library_instruction("owner_hands"),
        )
    raise ValueError("Linked Saga library spells have exactly three represented chapters")
