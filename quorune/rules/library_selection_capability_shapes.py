from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.fixed_library_selection_templates import (
    FIXED_LIBRARY_SELECTION_CAPABILITY,
    FIXED_LIBRARY_SELECTION_MECHANIC,
    LIBRARY_SELECTION_SUBTYPES,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec


_EFFECT_FIELDS = {
    "op",
    "player",
    "look_count",
    "public_reveal",
    "selected_reveal",
    "selection_policy",
    "minimum",
    "maximum",
    "predicate_groups",
    "remainder_destination",
    "remainder_order",
}
_POLICIES = {
    "fixed_any",
    "up_to_matching",
    "all_matching",
    "optional_slots",
}


FIXED_TOP_RANDOM_BOTTOM_VERSION = 11
RESOLVED_TOP_RANDOM_BOTTOM_VERSION = 26
TOP_RANDOM_BOTTOM_VERSIONS = (FIXED_TOP_RANDOM_BOTTOM_VERSION, RESOLVED_TOP_RANDOM_BOTTOM_VERSION)
FIXED_UP_TO_TWO_HAND_SELECTION_VERSION = 12
REVEALED_TOP_HAND_LIFE_VERSION = 13
PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_VERSION = 15
PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_CAPABILITY = "library.top.private_fixed_take_or_graveyard"
PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION = 16

_PRIVATE_FIXED_TOP_FILTERS = {
    "land": ObjectQuerySpec(zones=("library",), types_all=("land",)),
    "creature": ObjectQuerySpec(zones=("library",), types_all=("creature",)),
    "zombie": ObjectQuerySpec(zones=("library",), subtypes_any=("zombie",)),
}


def private_fixed_top_take_or_graveyard_effect(quality: str, *, player: str = "$controller") -> dict:
    return {
        "op": "fixed_library_selection",
        "schema_version": PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_VERSION,
        "player": player,
        "look_count": 1,
        "public_reveal": False,
        "selected_reveal": True,
        "selection_policy": "up_to_matching",
        "minimum": 0,
        "maximum": 1,
        "predicate_groups": [[_PRIVATE_FIXED_TOP_FILTERS[quality].to_dict()]],
        "remainder_destination": "optional_graveyard",
        "remainder_order": "chosen",
    }


def validate_private_fixed_top_take_or_graveyard(effect, *, player: str = "$controller") -> ObjectQuerySpec:
    from ..replacement.immutable import thaw_value

    actual = thaw_value(effect)
    if (
        not isinstance(actual, Mapping)
        or any(type(actual.get(field)) is not int for field in ("schema_version", "look_count", "minimum", "maximum"))
        or actual.get("public_reveal") is not False or actual.get("selected_reveal") is not True
    ):
        raise ValueError("The fixed top choice requires one private look and an optional revealed take")
    groups = actual.get("predicate_groups")
    if not (isinstance(groups, list) and len(groups) == 1
            and isinstance(groups[0], list) and len(groups[0]) == 1):
        raise ValueError("The fixed top choice requires one closed predicate")
    query = ObjectQuerySpec.from_dict(groups[0][0])
    match = next((quality for quality, expected in _PRIVATE_FIXED_TOP_FILTERS.items() if query == expected), None)
    if match is None or actual != private_fixed_top_take_or_graveyard_effect(match, player=player):
        raise ValueError("The fixed top choice requires its exact actor, filter and optional graveyard destination")
    return query


def private_one_hand_ordered_bottom_shape(effect, *, player="$controller") -> bool:
    """Match a mandatory private one-card result with a chosen bottom order."""
    return (
        set(effect) - {"schema_version"} == _EFFECT_FIELDS
        and effect.get("op") == "fixed_library_selection"
        and effect.get("player") == player
        and type(effect.get("look_count")) is int
        and effect["look_count"] > 0
        and effect.get("public_reveal") is False
        and effect.get("selected_reveal") is False
        and effect.get("selection_policy") == "fixed_any"
        and type(effect.get("minimum")) is int
        and type(effect.get("maximum")) is int
        and effect["minimum"] == effect["maximum"] == 1
        and effect.get("predicate_groups") in ([], ())
        and effect.get("remainder_destination") == "library_bottom"
        and effect.get("remainder_order") == "chosen"
    )


def revealed_top_hand_life_effect(*, player="$controller"):
    return {
        "op": "fixed_library_selection",
        "schema_version": REVEALED_TOP_HAND_LIFE_VERSION,
        "player": player,
        "life_loss": "revealed_top_mana_value",
    }


def validate_revealed_top_hand_life(effect, *, player="$controller"):
    if (not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int
            or dict(effect) != revealed_top_hand_life_effect(player=player)):
        raise ValueError("Reveal the current top card, move it to hand, then lose its mana value")



def fixed_up_to_two_hand_selection(look_count, *, player="$controller", remainder_destination):
    return {
        "op": "fixed_library_selection",
        "schema_version": FIXED_UP_TO_TWO_HAND_SELECTION_VERSION,
        "player": player,
        "look_count": look_count,
        "public_reveal": False,
        "selected_reveal": False,
        "selection_policy": "up_to_any",
        "minimum": 0,
        "maximum": min(2, look_count),
        "predicate_groups": [],
        "remainder_destination": remainder_destination,
        "remainder_order": "chosen" if remainder_destination == "graveyard" else "random",
    }


def validate_fixed_up_to_two_hand_selection(effect, *, player="$controller"):
    from ..replacement.immutable import thaw_value

    count = effect.get("look_count")
    destination = effect.get("remainder_destination")
    if (type(count) is not int or count < 1 or destination not in ("graveyard", "library_bottom")
            or destination == "graveyard" and count > 5
            or any(type(effect.get(field)) is not int for field in ("schema_version", "minimum", "maximum"))
            or effect.get("public_reveal") is not False or effect.get("selected_reveal") is not False
            or thaw_value(effect) != fixed_up_to_two_hand_selection(count, player=player, remainder_destination=destination)):
        raise ValueError("The private hand selection requires its exact count, choice and remainder")



def fixed_top_random_bottom_selection(look_count, *, player="$controller", version=FIXED_TOP_RANDOM_BOTTOM_VERSION):
    return {
        "op": "fixed_library_selection",
        "schema_version": version,
        "player": player,
        "look_count": look_count,
        "public_reveal": False,
        "selected_reveal": False,
        "selection_policy": "up_to_any",
        "minimum": 0,
        "maximum": 1,
        "predicate_groups": [],
        "selected_destination": "library_top",
        "remainder_destination": "library_bottom",
        "remainder_order": "random",
    }


def validate_fixed_top_random_bottom_selection(effect, *, player="$controller"):
    from ..replacement.immutable import thaw_value

    count = effect.get("look_count")
    version = effect.get("schema_version")
    if (type(count) is not int or count < 1 or version not in TOP_RANDOM_BOTTOM_VERSIONS
            or (version == FIXED_TOP_RANDOM_BOTTOM_VERSION and count > 5)
            or any(type(effect.get(field)) is not int for field in ("schema_version", "minimum", "maximum"))
            or effect.get("public_reveal") is not False or effect.get("selected_reveal") is not False
            or thaw_value(effect) != fixed_top_random_bottom_selection(count, player=player, version=version)):
        raise ValueError("A private top selection keeps at most one card and randomly bottoms the rest")


CHOSEN_TYPE_LIBRARY_SELECTION_VERSION = 5
CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY = "library.select.source_chosen_creature_type"
CHOSEN_TYPE_TOP_SELECTION_VERSION = 6
CHOSEN_TYPE_TOP_SELECTION_CAPABILITY = "library.top.source_chosen_creature_type"


def chosen_type_library_selection_effect() -> dict:
    from ..designation_prevention import DESIGNATION_LIBRARY_FILTER_KIND

    return {
        "op": "fixed_library_selection",
        "schema_version": CHOSEN_TYPE_LIBRARY_SELECTION_VERSION,
        "player": "$controller",
        "look_count": 3,
        "public_reveal": False,
        "selected_reveal": True,
        "selection_policy": "up_to_matching",
        "minimum": 0,
        "maximum": 1,
        "predicate_groups": [[{
            "kind": DESIGNATION_LIBRARY_FILTER_KIND, "schema_version": 1, "designation": "creature_type",
        }]],
        "remainder_destination": "library_bottom",
        "remainder_order": "random",
    }


def validate_chosen_type_library_selection(effect, *, actor: str | None = None) -> ObjectQuerySpec | None:
    from ..designation_prevention import chosen_type_library_predicate, unavailable_designation
    from ..replacement.immutable import thaw_value

    actual = thaw_value(effect)
    expected = chosen_type_library_selection_effect()
    if (
        not isinstance(actual, Mapping)
        or any(type(actual.get(field)) is not int for field in ("schema_version", "look_count", "minimum", "maximum"))
        or actual.get("public_reveal") is not False
        or actual.get("selected_reveal") is not True
    ):
        raise ValueError("Chosen-type library selection requires its exact typed top-three outcome")

    if actor is None:
        if actual != expected:
            raise ValueError("Chosen-type library selection requires its exact source designation")
        return None

    actual = dict(actual)
    groups = actual.pop("predicate_groups", None)
    expected.pop("predicate_groups")
    expected["player"] = actor
    if (actual != expected or not isinstance(groups, list) or len(groups) != 1
            or not isinstance(groups[0], list) or len(groups[0]) != 1):
        raise ValueError("Chosen-type library selection requires its exact actor and one filter")
    value = groups[0][0]
    if unavailable_designation(value):
        return None
    query = ObjectQuerySpec.from_dict(value)
    if len(query.subtypes_all) != 1 or query != chosen_type_library_predicate(query.subtypes_all[0]):
        raise ValueError("Chosen-type library selection requires the bound creature subtype")
    return query


def chosen_type_top_selection_effect(*, may_put_in_graveyard: bool) -> dict:
    from ..designation_prevention import DESIGNATION_LIBRARY_FILTER_KIND

    return {
        "op": "fixed_library_selection",
        "schema_version": CHOSEN_TYPE_TOP_SELECTION_VERSION,
        "player": "$controller",
        "look_count": 1,
        "public_reveal": False,
        "selected_reveal": True,
        "selection_policy": "up_to_matching",
        "minimum": 0,
        "maximum": 1,
        "predicate_groups": [[{
            "kind": DESIGNATION_LIBRARY_FILTER_KIND, "schema_version": 2, "designation": "creature_type",
            "creatures_only": not may_put_in_graveyard,
        }]],
        "remainder_destination": "optional_graveyard" if may_put_in_graveyard else "keep_top",
        "remainder_order": "chosen",
    }


def validate_chosen_type_top_selection(effect, *, actor: str | None = None) -> ObjectQuerySpec | None:
    from ..designation_prevention import chosen_type_library_predicate, unavailable_designation
    from ..replacement.immutable import thaw_value

    actual = thaw_value(effect)
    if (not isinstance(actual, Mapping)
            or any(type(actual.get(field)) is not int for field in ("schema_version", "look_count", "minimum", "maximum"))
            or actual.get("public_reveal") is not False or actual.get("selected_reveal") is not True):
        raise ValueError("Chosen-type top selection requires a private singleton and one optional revealed take")
    may_put_in_graveyard = actual.get("remainder_destination") == "optional_graveyard"
    expected = chosen_type_top_selection_effect(may_put_in_graveyard=may_put_in_graveyard)
    if actor is None:
        if actual != expected:
            raise ValueError("Chosen-type top selection requires its exact source filter and alternatives")
        return None

    actual = dict(actual)
    groups = actual.pop("predicate_groups", None)
    expected.pop("predicate_groups")
    expected["player"] = actor
    if (actual != expected or not isinstance(groups, list) or len(groups) != 1
            or not isinstance(groups[0], list) or len(groups[0]) != 1):
        raise ValueError("Chosen-type top selection requires its exact actor and filter")
    value = groups[0][0]
    if unavailable_designation(value):
        return None
    query = ObjectQuerySpec.from_dict(value)
    if len(query.subtypes_all) != 1 or query != chosen_type_library_predicate(
        query.subtypes_all[0], creatures_only=not may_put_in_graveyard,
    ):
        raise ValueError("Chosen-type top selection requires its bound subtype and creature scope")
    return query


def _characteristic_predicate(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        spec = ObjectQuerySpec.from_dict(value)
    except (ObjectQueryError, TypeError, ValueError):
        return False
    return bool(
        spec.zones == ("library",)
        and spec.owner is None
        and spec.controller is None
        and not spec.excluded_controllers
        and set((*spec.types_all, *spec.types_any, *spec.excluded_types)).issubset(
            {'artifact', 'battle', 'creature', 'enchantment', 'instant', 'kindred', 'land', 'planeswalker', 'sorcery'})
        and not spec.subtypes_all
        and set((*spec.subtypes_any, *spec.excluded_subtypes)).issubset(LIBRARY_SELECTION_SUBTYPES)
        and not spec.colors_all
        and not spec.keywords_all
        and not spec.keywords_none
        and spec.numeric_characteristic is None
        and set(spec.supertypes_all).issubset({'basic', 'legendary', 'snow'})
        and spec.token is None
        and spec.tapped is None
        and not spec.include_phased_out
        and spec.known_to_actor is None
        and spec.exclude_ref is None
        and spec.state_predicate is None
        and spec.minimum_color_count is None
        and bool(
            spec.types_all
            or spec.types_any
            or spec.excluded_types
            or spec.subtypes_any
            or spec.supertypes_all
            or spec.colors_any
            or spec.colorless is not None
        )
    )


def fixed_library_selection_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize only one closed fixed controller library selection."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_LIBRARY_SELECTION_MECHANIC not in mechanics
        or len(effects) != 1
        or target_schema is not None
    ):
        return ()
    effect = effects[0]
    from .library_play_partition import (
        PRIVATE_HAND_BOTTOM_EXILE_DEPENDENCIES, uses_private_hand_bottom_exile, validate_private_hand_bottom_exile,
    )

    if uses_private_hand_bottom_exile(effect):
        try:
            validate_private_hand_bottom_exile(effect)
        except (TypeError, ValueError):
            return ()
        return PRIVATE_HAND_BOTTOM_EXILE_DEPENDENCIES
    from .blocked_damage_cast import represents_window, window_dependencies

    if represents_window(effect):
        try:
            return window_dependencies(effect)
        except (TypeError, ValueError, KeyError):
            return ()
    if (type(effect.get("schema_version")) is int
            and effect["schema_version"] == PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION):
        return (FIXED_LIBRARY_SELECTION_CAPABILITY,) if private_one_hand_ordered_bottom_shape(effect) else ()
    from ..linked_library_imprint_model import uses_library_imprint_selection
    from .linked_library_imprint import library_imprint_node_capabilities

    if uses_library_imprint_selection(effect):
        return library_imprint_node_capabilities(effects=effects, target_schema=target_schema)
    from .one_revealed_card_placement import (
        OneRevealedCardPlacement, REVEAL_ONE_TOP_OPERATION,
    )

    if effect.get("op") == REVEAL_ONE_TOP_OPERATION:
        try:
            return OneRevealedCardPlacement.from_effect(effect).capabilities
        except (TypeError, ValueError):
            return ()
    if effect.get("schema_version") == PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_VERSION:
        try:
            validate_private_fixed_top_take_or_graveyard(effect)
        except (TypeError, ValueError):
            return ()
        return (FIXED_LIBRARY_SELECTION_CAPABILITY, PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_CAPABILITY)
    from .repeat_top_hand_life import (
        REPEAT_TOP_HAND_LIFE_CAPABILITY, REPEAT_TOP_HAND_LIFE_FIELD,
        validate_repeat_top_hand_life,
    )

    if REPEAT_TOP_HAND_LIFE_FIELD in effect:
        try:
            if validate_repeat_top_hand_life(effect) == "start":
                return (FIXED_LIBRARY_SELECTION_CAPABILITY, REPEAT_TOP_HAND_LIFE_CAPABILITY, "life.change.effect")
        except (TypeError, ValueError):
            pass
        return ()
    from .private_look_land_or_hand import PRIVATE_LOOK_LAND_OR_HAND_VERSION, private_land_hand_capabilities

    if effect.get("schema_version") == PRIVATE_LOOK_LAND_OR_HAND_VERSION:
        return private_land_hand_capabilities(effect)
    if effect.get("schema_version") == REVEALED_TOP_HAND_LIFE_VERSION:
        try:
            validate_revealed_top_hand_life(effect)
        except (TypeError, ValueError):
            return ()
        return (FIXED_LIBRARY_SELECTION_CAPABILITY, "life.change.effect")
    if effect.get("schema_version") == FIXED_UP_TO_TWO_HAND_SELECTION_VERSION:
        try:
            validate_fixed_up_to_two_hand_selection(effect)
        except (TypeError, ValueError):
            return ()
        return (FIXED_LIBRARY_SELECTION_CAPABILITY,)
    if effect.get("schema_version") == FIXED_TOP_RANDOM_BOTTOM_VERSION:
        try:
            validate_fixed_top_random_bottom_selection(effect)
        except (TypeError, ValueError):
            return ()
        return (FIXED_LIBRARY_SELECTION_CAPABILITY,)
    if effect.get("schema_version") == CHOSEN_TYPE_LIBRARY_SELECTION_VERSION:
        try:
            validate_chosen_type_library_selection(effect)
        except (TypeError, ValueError):
            return ()
        return (FIXED_LIBRARY_SELECTION_CAPABILITY, CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY)
    if effect.get("schema_version") == CHOSEN_TYPE_TOP_SELECTION_VERSION:
        try:
            validate_chosen_type_top_selection(effect)
        except (TypeError, ValueError):
            return ()
        return (FIXED_LIBRARY_SELECTION_CAPABILITY, CHOSEN_TYPE_TOP_SELECTION_CAPABILITY)
    from ..library_battlefield_model import (
        LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION, PRIVATE_TOP_LAND_VERSIONS, library_battlefield_payload_dependencies,
        RANDOM_REVEALED_CREATURE_ENTRY_VERSION,
    )
    if type(effect.get('schema_version')) is int and effect['schema_version'] in (
        3, LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION, *PRIVATE_TOP_LAND_VERSIONS,
        RANDOM_REVEALED_CREATURE_ENTRY_VERSION,
    ):
        try:required=library_battlefield_payload_dependencies(effect)
        except (TypeError,ValueError,KeyError):return ()
        return required if effect.get('player')=='$controller' else ()
    version_two=effect.get('schema_version')==2 and type(effect.get('schema_version')) is int
    expected=_EFFECT_FIELDS|{'schema_version','selected_destination'} if version_two else _EFFECT_FIELDS
    if set(effect) != expected or version_two and (effect.get('selected_destination')!='library_top'
            or effect.get('remainder_destination')!='exile' or effect.get('selection_policy')!='up_to_any'
            or type(effect.get('minimum')) is not int or effect['minimum']!=0
            or type(effect.get('maximum')) is not int or effect['maximum']!=1
            or effect.get('remainder_order')!='chosen' or effect.get('public_reveal') is not False
            or effect.get('selected_reveal') is not False):
        return ()
    look_count = effect.get("look_count")
    minimum = effect.get("minimum")
    maximum = effect.get("maximum")
    policy = effect.get("selection_policy")
    groups = effect.get("predicate_groups")
    if (
        effect.get("op") != "fixed_library_selection"
        or effect.get("player") != "$controller"
        or type(look_count) is not int
        or look_count <= 0
        or type(effect.get("public_reveal")) is not bool
        or type(effect.get("selected_reveal")) is not bool
        or policy not in (_POLICIES|{'up_to_any'} if version_two else _POLICIES)
        or type(minimum) is not int
        or type(maximum) is not int
        or minimum < 0
        or maximum <= 0
        or minimum > maximum
        or maximum > look_count
        or effect.get("remainder_destination")
        not in ({'exile'} if version_two else {"graveyard", "library_bottom"})
        or effect.get("remainder_order") not in {"chosen", "random"}
        or not isinstance(groups, (list, tuple))
    ):
        return ()
    parsed_groups = tuple(groups)
    if any(
        not isinstance(group, (list, tuple))
        or not group
        or any(not _characteristic_predicate(value) for value in group)
        for group in parsed_groups
    ):
        return ()
    if policy=='up_to_any':
        valid_policy=version_two and not parsed_groups
    elif policy == "fixed_any":
        valid_policy = not parsed_groups and minimum == maximum
    elif policy == "optional_slots":
        valid_policy = (
            len(parsed_groups) == maximum
            and minimum == 0
            and len(parsed_groups) > 1
        )
    else:
        valid_policy = len(parsed_groups) == 1 and (
            policy != "all_matching" or minimum == 0
        )
    return (FIXED_LIBRARY_SELECTION_CAPABILITY,) if valid_policy else ()


__all__ = ["fixed_library_selection_node_capabilities"]
