from __future__ import annotations

"""Bound three-counter allocation for an ordinary printed triggered ability."""

from collections.abc import Mapping

from .replacement.immutable import FrozenMap


COUNTER_ALLOCATION_FIELD = "target_counter_allocation"
ANNOUNCED_COUNTER_CONTEXT = "announced_counter_allocation"
BOUND_COUNTER_SHARES = "counter_target_shares"
ANNOUNCED_COUNTER_KIND = "announced_three_counter_target_allocation"
ANNOUNCED_COUNTER_CAPABILITY = "counter.producer.announced_three_target_allocation"
ANNOUNCED_COUNTER_MECHANIC = "announced-three-counter-target-allocation"


def announced_counter_target_schema():
    return {
        "groups": [{
            "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
            "controller_relation": "you", "min": 1, "max": 3,
        }],
        COUNTER_ALLOCATION_FIELD: {"schema_version": 1, "total": 3},
    }


def announced_counter_effect():
    return {
        "op": "place_counters_on_targets", "schema_version": 2, "cards": "$targets",
        "maximum_targets": 3, "counter": "+1/+1", "source": "$source",
        "amount": {"kind": ANNOUNCED_COUNTER_KIND, "schema_version": 1},
    }


def uses_announced_counter_amount(value):
    amount = value.get("amount")
    return isinstance(amount, Mapping) and amount.get("kind") == ANNOUNCED_COUNTER_KIND


def validate_announced_counter_effect(effect):
    if (FrozenMap(effect) != FrozenMap(announced_counter_effect())
            or type(effect["schema_version"]) is not int or type(effect["maximum_targets"]) is not int
            or type(effect["amount"]["schema_version"]) is not int):
        raise ValueError("Three-counter distribution requires its exact original target instruction")


def validate_announced_counter_schema(schema):
    if (not isinstance(schema, Mapping) or FrozenMap(schema) != FrozenMap(announced_counter_target_schema())
            or any(type(schema["groups"][0][key]) is not int for key in ("min", "max"))
            or any(type(schema[COUNTER_ALLOCATION_FIELD][key]) is not int for key in ("schema_version", "total"))):
        raise ValueError("Three-counter distribution requires one exact controlled creature target group")


def _validate_shares(shares, *, count, original):
    if (type(count) is not int or not 1 <= count <= 3
            or not isinstance(shares, (list, tuple)) or len(shares) != count
            or any(type(value) is not int or value < 1 for value in shares)):
        raise ValueError("Each selected creature needs a positive exact counter share")
    total = sum(shares)
    if total > 3 or original and total != 3:
        raise ValueError("Distribute all three counters across the selected creatures")


def announce_counter_allocation(schema, targets, response, *, trigger_creation):
    if not isinstance(schema, Mapping) or COUNTER_ALLOCATION_FIELD not in schema:
        if COUNTER_ALLOCATION_FIELD in response:
            raise ValueError("This ability does not announce a counter allocation")
        return {}
    validate_announced_counter_schema(schema)
    if not trigger_creation:
        raise ValueError("This counter allocation must be announced when the trigger is placed")
    submitted = response.get(COUNTER_ALLOCATION_FIELD)
    if not isinstance(submitted, Mapping) or set(submitted) != set(targets) or len(submitted) != len(targets):
        raise ValueError("The counter allocation must name each originally selected target exactly")
    shares = [submitted[ref] for ref in targets]
    _validate_shares(shares, count=len(targets), original=True)
    return {ANNOUNCED_COUNTER_CONTEXT: {"schema_version": 1, "shares": shares}}


def announce_target_allocations(schema, targets, response, *, trigger_creation):
    from .announced_damage import announced_allocation_details

    return {
        **announced_allocation_details(schema, targets, response),
        **announce_counter_allocation(schema, targets, response, trigger_creation=trigger_creation),
    }


def read_announced_counter_shares(value, *, count):
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "shares"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("The recorded counter allocation is malformed")
    shares = value["shares"]
    _validate_shares(shares, count=count, original=True)
    return tuple(shares)


def public_target_allocation_shares(item):
    from .announced_damage import ANNOUNCED_DAMAGE_CONTEXT

    result = {}
    for context_key, public_key in (
        (ANNOUNCED_DAMAGE_CONTEXT, "damage_shares"), (ANNOUNCED_COUNTER_CONTEXT, "counter_shares"),
    ):
        allocation = item.context.get(context_key)
        shares = allocation.get("shares") if isinstance(allocation, Mapping) else None
        if (isinstance(shares, (list, tuple)) and len(shares) == len(item.targets)
                and all(type(share) is int and share > 0 for share in shares)):
            result[public_key] = list(shares)
    return result


def bind_announced_counter_allocation(effect, item):
    if not uses_announced_counter_amount(effect):
        return effect
    validate_announced_counter_effect(effect)
    if item.context.get("targets_revalidated") is not True:
        raise ValueError("Announced counters require revalidated original target slots")
    shares = read_announced_counter_shares(item.context.get(ANNOUNCED_COUNTER_CONTEXT), count=len(item.targets))
    bound = {ref: amount for ref, amount in zip(item.targets, shares, strict=True) if ref is not None}
    if len(bound) != sum(ref is not None for ref in item.targets):
        raise ValueError("Announced counter targets must remain distinct")
    return {**effect, "amount": 1, BOUND_COUNTER_SHARES: bound}


def validate_bound_counter_shares(shares, *, refs, counter_name, amount, maximum_targets):
    if (maximum_targets != 3 or type(maximum_targets) is not int or counter_name != "+1/+1"
            or type(amount) is not int or amount != 1 or not isinstance(shares, Mapping)
            or any(type(ref) is not str or not ref for ref in shares)
            or set(shares) != set(refs) or len(shares) != len(refs)):
        raise ValueError("The surviving counter shares must match their exact original target references")
    _validate_shares(tuple(shares.values()), count=len(refs), original=False)
    return FrozenMap(shares)
