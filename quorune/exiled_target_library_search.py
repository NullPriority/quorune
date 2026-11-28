from __future__ import annotations

"""A fixed private basic-land selection that joins an earlier linked exile."""

from collections.abc import Mapping
from typing import Any

from .library_order_model import SHUFFLE_CAPABILITY, fixed_shuffle_effect
from .library_search_model import (
    EXILED_TARGET_SEARCH_VERSION,
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FixedCountedLibrarySearchTemplate,
)
from .linked_exile_return_model import (
    EXILED_TARGET_SEARCH_BINDING,
    LINKED_EXILE_RETURN_CAPABILITY,
    LINKED_EXILE_RETURN_OPERATION,
    LinkedExileReturnSpec,
)
from .replacement.immutable import FrozenMap, thaw_value

EXILED_TARGET_SEARCH_CAPABILITY = "library.search.basic_land_with_exiled_target_entry"
EXILED_TARGET_SEARCH_MECHANIC = "basic-land-search-with-exiled-target"
EXILED_TARGET_SEARCH_DEPENDENCIES = (
    EXILED_TARGET_SEARCH_CAPABILITY,
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    LINKED_EXILE_RETURN_CAPABILITY,
    SHUFFLE_CAPABILITY,
    "target.revalidate_resolution",
    "attachment.reference.current_or_lki",
    "trigger.event.normalized_damage",
    "trigger.effect.fixed_event",
    "trigger.placement.apnap",
)
EXILED_TARGET_RETURN_SPEC = LinkedExileReturnSpec(controller="actor")

_BASIC_LAND_SEARCH = FixedCountedLibrarySearchTemplate(
    count=1,
    optional_count=False,
    selector=FrozenMap({"types": ["land"], "supertypes": ["basic"]}),
    destination="battlefield",
    reveal=False,
)


def owned_creature_search_target() -> dict[str, Any]:
    return {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "creature": True,
        "owner": "you",
        "min": 0,
        "max": 1,
    }


def basic_land_search_with_exiled_target() -> dict[str, Any]:
    return {
        **_BASIC_LAND_SEARCH.effect(),
        "schema_version": EXILED_TARGET_SEARCH_VERSION,
        "shuffle_after": False,
        "exile_binding": EXILED_TARGET_SEARCH_BINDING,
    }


def exiled_target_search_effects() -> tuple[dict[str, Any], ...]:
    linked = {
        "op": LINKED_EXILE_RETURN_OPERATION,
        "binding_id": EXILED_TARGET_SEARCH_BINDING,
        "spec": EXILED_TARGET_RETURN_SPEC.to_dict(),
    }
    return (
        {**linked, "phase": "exile", "cards": "$targets"},
        basic_land_search_with_exiled_target(),
        {**linked, "phase": "return"},
        fixed_shuffle_effect(),
    )


def validate_basic_land_search_with_exiled_target(effect: Mapping[str, Any]) -> None:
    if not isinstance(effect, Mapping):
        raise ValueError("Combined entry requires a search descriptor")
    value = thaw_value(effect)
    version = value.get("schema_version")
    count = value.get("count")
    if (
        type(version) is not int or version != EXILED_TARGET_SEARCH_VERSION
        or not isinstance(count, Mapping) or any(type(amount) is not int for amount in count.values())
        or any(type(value.get(name)) is not bool for name in ("reveal", "shuffle_before_placement", "shuffle_after"))
        or value != basic_land_search_with_exiled_target()
    ):
        raise ValueError("Combined entry requires the exact bound basic-land search")
