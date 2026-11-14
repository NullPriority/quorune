"""Prove both replacement branches through existing fixed result owners."""

from ..conditional_target_preparation import preparation_branch_capabilities
from ..object_query import ObjectQueryResult, object_matches_query
from ..replacement.immutable import thaw_value
from ..target_replacement_model import (
    CURRENT_TARGET_REPLACEMENT_CAPABILITY, CURRENT_TARGET_REPLACEMENT_MECHANIC,
    validate_current_target_replacement,
)


def current_target_replacement_matches(condition, row):
    if condition.scope != "target" or not isinstance(row, ObjectQueryResult):
        raise ValueError("A current target predicate requires typed public characteristics")
    return any(object_matches_query(row, query) for query in condition.queries_any)


def current_target_replacement_branch_capabilities(effects, mechanics, binding, *, subject="$target.0"):
    owners = set()
    for child in effects:
        owned = preparation_branch_capabilities((child,), mechanics, binding, subject=subject)
        if not owned:
            return ()
        owners.update(owned)
    return tuple(sorted(owners))


def current_target_replacement_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if (CURRENT_TARGET_REPLACEMENT_MECHANIC not in mechanics
            or "fixed-resolution-public-condition" not in mechanics or len(effects) != 1):
        return ()
    wrapper = effects[0]
    try:
        condition, _ = validate_current_target_replacement(wrapper)
    except (TypeError, ValueError, KeyError):
        return ()
    if (wrapper["player"] != "$controller" or wrapper["condition_target"] != "$target.0"
            or thaw_value(wrapper["target_binding"]) != thaw_value(target_schema)):
        return ()
    owners = {CURRENT_TARGET_REPLACEMENT_CAPABILITY, "resolution.effect.public_condition"}
    for branch, key in (("effects", "mechanic_ids"), ("otherwise_effects", "otherwise_mechanic_ids")):
        if not set(wrapper[key]) <= mechanics:
            return ()
        owned = current_target_replacement_branch_capabilities(wrapper[branch], wrapper[key], target_schema)
        if not owned or any(child.get("op") == "place_counters" and child.get("source") != "$source" for child in wrapper[branch]):
            return ()
        owners.update(owned)
    if condition.scope == "night":
        owners.add("card_form.day_night")
    else:
        from ..object_predicate import object_query_capabilities
        for query in condition.queries_any:
            owners.update(object_query_capabilities(query))
    return tuple(sorted(owners))
