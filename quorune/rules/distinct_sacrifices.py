from __future__ import annotations

"""Closed resolution instructions and matching for distinct sacrifice roles."""

from collections import Counter
from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .affected_player_discard_capability_shapes import fixed_controller_discard_node_capabilities


DISTINCT_SACRIFICE_FIELD = "distinct_sacrifice"
DISTINCT_SACRIFICE_CAPABILITY = "choice.permanent.distinct_sacrifice_roles"
DISTINCT_SACRIFICE_ROLES = ("creature", "artifact", "land")
_EFFECT_FIELDS = frozenset({"op", "player", DISTINCT_SACRIFICE_FIELD})
_DISCARD_MECHANICS = ("fixed-controller-card-discard", "fixed-affected-player-discard", "cr-402-hand")


def distinct_sacrifice_effect(effect, *, actor="$controller"):
    if (not isinstance(effect, Mapping) or set(effect) != _EFFECT_FIELDS
            or effect["op"] != "choose_objects" or type(effect["player"]) is not str or effect["player"] != actor):
        raise ValueError("Distinct sacrifices require a closed controller result")
    value = effect[DISTINCT_SACRIFICE_FIELD]
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "roles", "before"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["roles"], (list, tuple)) or tuple(value["roles"]) != DISTINCT_SACRIFICE_ROLES):
        raise ValueError("Distinct sacrifices require the fixed ordered permanent roles")
    before = value["before"]
    if before is None:
        return DISTINCT_SACRIFICE_ROLES, None, ()
    if (not isinstance(before, Mapping) or before.get("actor") != actor
            or not isinstance(before.get("players"), (list, tuple)) or tuple(before["players"]) != (actor,)
            or before.get("count") != 1):
        raise ValueError("The preceding discard must choose one card for the same controller")
    discard = {**thaw_value(before), "actor": "$controller", "players": ["$controller"]}
    required = fixed_controller_discard_node_capabilities(effects=(discard,), target_schema=None, mechanic_ids=_DISCARD_MECHANICS)
    if not required or discard["predicate"] != ObjectQuerySpec(zones=("hand",)).to_dict():
        raise ValueError("The preceding discard must use the unrestricted private hand choice")
    return DISTINCT_SACRIFICE_ROLES, before, required


def distinct_sacrifice_payload_dependencies(effect):
    if DISTINCT_SACRIFICE_FIELD not in effect:
        return ()
    _, _, discard = distinct_sacrifice_effect(effect)
    return (DISTINCT_SACRIFICE_CAPABILITY, "choice.affected_player.fixed_sacrifice", *discard)


def distinct_sacrifice_node_capabilities(*, effects, target_schema):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        return distinct_sacrifice_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_distinct_sacrifice_program(program):
    required = distinct_sacrifice_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def role_masks(candidates, roles):
    return {ref: sum(1 << index for index, role in enumerate(roles) if role in types)
            for ref, (_, types) in candidates.items()}


def maximum_sacrifices(counts, role_count):
    # Each reachable mask represents the distinct roles covered so far. Copies
    # of an identical type mask beyond the role count cannot improve a result.
    reached = {0}
    full = (1 << role_count) - 1
    for mask, count in counts.items():
        if not mask:
            continue
        for _ in range(min(count, role_count)):
            reached |= {covered | (1 << slot) for covered in reached for slot in range(role_count)
                        if mask & (1 << slot) and not covered & (1 << slot)}
            if full in reached:
                return role_count
    return max(mask.bit_count() for mask in reached)


def distinct_sacrifice_options(masks, role_count):
    counts = Counter(masks.values())
    need = maximum_sacrifices(counts, role_count)
    tail = Counter(mask >> 1 for mask in masks.values())
    may_skip = maximum_sacrifices(tail, role_count - 1) == need
    allowed_masks = set()
    for mask in counts:
        if mask & 1:
            tail[mask >> 1] -= 1
            if 1 + maximum_sacrifices(tail, role_count - 1) == need:
                allowed_masks.add(mask)
            tail[mask >> 1] += 1
    return tuple(sorted(ref for ref, mask in masks.items() if mask in allowed_masks)), may_skip, need
