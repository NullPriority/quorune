from __future__ import annotations

"""Capability shapes for closed direct permanent-counter removal nodes."""

from typing import Any, Iterable, Mapping, Sequence

from ..counter_removal import (
    ALL_PERMANENT_COUNTERS_THEN_TOKEN_EXILE_FIELD, validate_all_permanent_counters_then_token_exile,
)

from .node_capability_shapes import (
    direct_target_predicate_capabilities,
    fixed_counter_target_schema_is_closed,
)


def fixed_counter_removal_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one closed fixed counter removal."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if len(effects) == 1 and 'cr-122-counters' in mechanics and target_schema is None:
        effect = effects[0]
        if (set(effect) == {'op', 'card', 'counter', 'amount', 'source'}
                and effect.get('op') == 'remove_counters'
                and effect.get('card') == '$source.zone_object'
                and type(effect.get('counter')) is str and effect['counter'].strip() == effect['counter']
                and effect['counter']
                and type(effect.get('amount')) is int and effect['amount'] > 0
                and effect.get('source') == '$source'):
            return ('counter.removal.fixed_effect', 'counter.removal.fixed_source_effect')
    if (
        not {"cr-122-counters", "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "counter", "amount", "source"}
        or effect.get("op") != "remove_counters"
        or effect.get("card") != "$target.0"
        or type(effect.get("counter")) is not str
        or not effect.get("counter")
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
        or effect.get("source") != "$source"
        or not fixed_counter_target_schema_is_closed(target_schema)
    ):
        return ()
    assert target_schema is not None
    return (
        "counter.removal.fixed_effect",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def all_permanent_counters_then_token_exile_dependencies(effects, target_schema, mechanics):
    if (len(effects) != 1 or target_schema is not None
            or not {"cr-122-counters", "fixed-public-zone-move", "fixed-public-zone-move-set"} <= set(mechanics)):
        return ()
    try:
        validate_all_permanent_counters_then_token_exile(effects[0])
    except (TypeError, ValueError):
        return ()
    return ("counter.removal.all_effect", "zone.move.fixed_public_set")


def all_counter_removal_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one direct all-counter removal."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if len(effects) == 1 and isinstance(effects[0], Mapping) and ALL_PERMANENT_COUNTERS_THEN_TOKEN_EXILE_FIELD in effects[0]:
        return all_permanent_counters_then_token_exile_dependencies(effects, target_schema, mechanics)
    if (len(effects) == 1 and (effects[0].get('schema_version') == 3
            or isinstance(target_schema, Mapping) and 'up_to' in target_schema)):
        from .target_all_counter_removal import target_all_counter_variant_capabilities
        return target_all_counter_variant_capabilities(effects=effects, target_schema=target_schema,
            mechanic_ids=mechanics)
    if len(effects) == 1 and 'cr-122-counters' in mechanics and target_schema is None:
        from ..counter_names import normalized_counter_name
        effect = effects[0]
        name = effect.get('counter')
        if (set(effect) == {'op', 'schema_version', 'card', 'counter', 'source'}
                and effect.get('op') == 'remove_all_counters'
                and type(effect.get('schema_version')) is int and effect['schema_version'] == 2
                and effect.get('card') == '$source.zone_object' and effect.get('source') == '$source'
                and type(name) is str and name and normalized_counter_name(name) == name):
            return ('counter.removal.all_effect', 'counter.removal.all_named_source_effect')
    if (
        not {"cr-122-counters", "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "source"}
        or effect.get("op") != "remove_all_counters"
        or effect.get("card") != "$target.0"
        or effect.get("source") != "$source"
        or not fixed_counter_target_schema_is_closed(target_schema)
    ):
        return ()
    assert target_schema is not None
    return (
        "counter.removal.all_effect",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


__all__ = [
    "all_counter_removal_node_capabilities",
    "fixed_counter_removal_node_capabilities",
]
