from __future__ import annotations

"""Exact public greatest-power target envelopes over existing result owners."""

from collections.abc import Mapping

from ..relative_power_target import GREATEST_CREATURE_POWER_PREDICATE
from ..replacement.immutable import FrozenMap, thaw_value


GREATEST_POWER_TARGET_CAPABILITY = "quantity_expression.public_characteristic_aggregate"
_GLOBAL_EXILE = ({"op": "exile_permanent", "card": "$target.0"},)
_CONTROLLED_COUNTER = ({"op": "place_counters", "card": "$target.0",
                       "counter": "+1/+1", "amount": 1, "source": "$source"},)
_CONTROLLED_KEYWORDS = tuple(
    {"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": keyword}
    for keyword in ("Flying", "First Strike", "Lifelink")
)


def _unranked_schema(effects, target_schema):
    if not isinstance(target_schema, Mapping) or target_schema.get("predicate") != GREATEST_CREATURE_POWER_PREDICATE:
        raise ValueError("Greatest-power effects require their public target predicate")
    schema = thaw_value(FrozenMap(target_schema))
    schema.pop("predicate")
    expected = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]}
    if type(schema.get("count")) is not int:
        raise ValueError("Greatest-power effects require exactly one creature target")
    children = tuple(thaw_value(FrozenMap(effect)) for effect in effects)
    if schema == expected and children == _GLOBAL_EXILE:
        return schema
    if schema == {**expected, "controller_relation": "you"} and (
        children == _CONTROLLED_KEYWORDS
        or children == _CONTROLLED_COUNTER and type(children[0]["amount"]) is int
    ):
        return schema
    raise ValueError("Greatest-power target does not match its complete fixed result and controller scope")


def greatest_power_target_node_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    from .capabilities import capability_dependencies_for_node

    try:
        original = _unranked_schema(effects, target_schema)
    except (TypeError, ValueError):
        return ()
    owned = capability_dependencies_for_node(
        effects=effects, target_schema=original, mechanic_ids=mechanics, cost_schema=cost_schema,
    )
    if not owned:
        return ()
    return tuple(sorted({*owned, GREATEST_POWER_TARGET_CAPABILITY, "target.revalidate_resolution"}))


def greatest_power_target_runtime_dependencies(program):
    schema = program.target_schema
    if not isinstance(schema, Mapping) or schema.get("predicate") != GREATEST_CREATURE_POWER_PREDICATE:
        return ()
    _unranked_schema(program.effects, schema)
    return (GREATEST_POWER_TARGET_CAPABILITY, "target.revalidate_resolution")
