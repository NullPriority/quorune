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
    from ..library_battlefield_model import LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION, library_battlefield_payload_dependencies
    if type(effect.get('schema_version')) is int and effect['schema_version'] in (3,LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION):
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
