from __future__ import annotations

"""Closed capability shapes for fixed positive damage instructions."""

from typing import Any, Iterable, Mapping, Sequence

from ..announced_damage import (
    ANNOUNCED_DAMAGE_CAPABILITY,
    ANNOUNCED_DAMAGE_EFFECT_FIELD,
    DAMAGE_ALLOCATION_FIELD,
    VariableDamageAllocationSpec,
    damage_allocation_spec,
    validate_announced_damage_effect,
    variable_damage_allocation,
)
from ..compiler.direct_target import DirectPermanentTargetSpec
from ..damage_death_exile_model import (
    DAMAGE_DEATH_EXILE_CAPABILITY, DAMAGE_DEATH_EXILE_FIELD, DAMAGE_DEATH_EXILE_MECHANIC,
    DamageDeathExileScope, damage_death_exile_scope,
)
from ..fixed_damage_set_model import (
    FixedDamageSetError,
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
    PlayerDamageGroup,
)
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


_FIXED_DAMAGE_TARGET_SCHEMAS: dict[str, Mapping[str, Any]] = {
    "any_target": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "damageable",
        "count": 1,
    },
    "creature": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature"],
        "count": 1,
    },
    "creature_or_planeswalker": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature", "planeswalker"],
        "count": 1,
    },
    "player_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
    },
    "opponent_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
        "player_relation": "opponent",
    },
    "player": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    },
    "opponent": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent",
    },
}
_PLAYER_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
        "player",
        "opponent",
    }
)
_PERMANENT_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "creature",
        "creature_or_planeswalker",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
    }
)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _fixed_set_damage_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...] | None:
    if not (
        set(effect) == {"op", "source", "amount", "groups"}
        and effect.get("op") == "damage_fixed_set"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        return None
    try:
        spec = FixedDamageSetSpec.from_dict({"groups": effect.get("groups")})
    except FixedDamageSetError:
        return ()
    targeted = any(
        isinstance(group, PermanentDamageGroup)
        and group.controller_relation is PermanentControllerRelation.TARGET_PLAYER
        for group in spec.groups
    )
    player_targets = (
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
        },
        {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": "opponent",
            "count": 1,
        },
    )
    valid_target = (
        not targeted and target_schema is None
        or targeted and dict(target_schema or {}) in player_targets
    )
    if not valid_target or targeted and "cr-115-targets" not in mechanics:
        return ()
    dependencies = {"damage.amount.positive", "damage.batch.fixed_set", "damage.source.stack_current_or_lki"}
    if any(isinstance(group, PlayerDamageGroup) for group in spec.groups):
        dependencies.add("damage.result.player_life")
    permanent_groups = tuple(
        group for group in spec.groups if isinstance(group, PermanentDamageGroup)
    )
    if permanent_groups:
        dependencies.add("damage.result.multitype_permanent")
    if targeted:
        dependencies.add("target.revalidate_resolution")
    if any(_uses_characteristic_predicate(group) for group in permanent_groups):
        dependencies.add("target.permanent.characteristic_predicate")
    if any(group.query.state_predicate is not None for group in permanent_groups):
        dependencies.add("state_query.permanent.public_state_predicate")
    return tuple(sorted(dependencies))


def _uses_characteristic_predicate(group: PermanentDamageGroup) -> bool:
    query = group.query
    return bool(
        query.excluded_types
        or query.subtypes_any
        or query.excluded_subtypes
        or query.supertypes_all
        or query.colors_all
        or query.colors_any
        or query.colorless is not None
        or query.minimum_color_count is not None
        or query.keywords_all
        or query.keywords_none
        or query.token is not None
        or group.exclude_source
    )


def _fixed_direct_damage_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...]:
    if (
        "cr-115-targets" not in mechanics
        or set(effect) != {"op", "source", "target", "amount"}
        or effect.get("op") != "damage"
        or effect.get("source") != "$source"
        or effect.get("target") != "$target.0"
        or not _positive_int(effect.get("amount"))
    ):
        return ()
    schema = dict(target_schema or {})
    domain = next(
        (
            name
            for name, expected in _FIXED_DAMAGE_TARGET_SCHEMAS.items()
            if schema == expected
        ),
        None,
    )
    direct_target = None
    if domain is None and direct_permanent_target_schema_is_closed(schema):
        direct_target = DirectPermanentTargetSpec.from_target_schema(schema)
    if domain is None and direct_target is None:
        return ()
    dependencies = {"damage.amount.positive", "damage.source.stack_current_or_lki"}
    if domain in _PLAYER_DAMAGE_DOMAINS:
        dependencies.add("damage.result.player_life")
    if domain in _PERMANENT_DAMAGE_DOMAINS or direct_target is not None:
        dependencies.add("damage.result.multitype_permanent")
    if direct_target is not None:
        dependencies.update(direct_target_predicate_capabilities(schema))
        dependencies.add("target.revalidate_resolution")
    else:
        dependencies.add(
            "target.public.player_or_damageable_permanent"
            if domain == "any_target"
            else "target.revalidate_resolution"
        )
    return tuple(sorted(dependencies))


def damage_death_exile_scope_is_closed(effect, target_schema) -> bool:
    try:
        scope = damage_death_exile_scope(effect)
    except ValueError:
        return False
    if effect.get('op') == 'damage_fixed_set':
        return scope is DamageDeathExileScope.DEALT_CREATURES
    if effect.get('op') != 'damage' or effect.get('target') != '$target.0':
        return False
    if scope is DamageDeathExileScope.DEALT_CREATURES:
        return True
    if scope is not DamageDeathExileScope.TARGET:
        return False
    if target_schema == _FIXED_DAMAGE_TARGET_SCHEMAS['creature']:
        return True
    if not direct_permanent_target_schema_is_closed(target_schema):
        return False
    direct = DirectPermanentTargetSpec.from_target_schema(target_schema)
    return 'creature' in direct.types_all or direct.types_any == ('creature',)


def _announced_damage_capabilities(effect, target_schema, mechanics) -> tuple[str, ...]:
    schema = dict(target_schema or {})
    if set(schema) != {"groups", DAMAGE_ALLOCATION_FIELD}:
        return ()
    try:
        spec = validate_announced_damage_effect(effect)
        if damage_allocation_spec(schema) != spec:
            return ()
        group = dict(schema["groups"][0])
    except (TypeError, ValueError):
        return ()
    minimum, maximum = group.get("min"), group.get("max")
    if "count" in group or type(minimum) is not int or type(maximum) is not int:
        return ()
    if isinstance(spec, VariableDamageAllocationSpec):
        if minimum != 0 or maximum not in (0, 2):
            return ()
        amount = 1
        allocation_capabilities = spec.capability_dependencies
    else:
        if not 0 <= minimum <= maximum <= spec.total or maximum == 0:
            return ()
        amount = spec.total
        allocation_capabilities = (ANNOUNCED_DAMAGE_CAPABILITY,)
    del group["min"], group["max"]
    group["count"] = 1
    direct = {"op": "damage", "source": "$source", "target": "$target.0", "amount": amount}
    base = _fixed_direct_damage_capabilities(direct, group, mechanics)
    if not base:
        return ()
    return tuple(sorted({*base, *allocation_capabilities, "damage.batch.fixed_set", "target.revalidate_resolution"}))


def variable_damage_allocation_program_requirements(program) -> tuple[str, ...]:
    schema = program.target_schema
    if (program.event != "resolve" or not program.ability_id.startswith("spell:")
            or len(program.effects) != 1 or variable_damage_allocation(schema) is None):
        raise ValueError("Variable damage allocation requires one complete cast-time spell result")
    required = _announced_damage_capabilities(program.effects[0], schema, program.coverage)
    if not required:
        raise ValueError("Variable damage allocation requires matching effect and target totals")
    return required


def fixed_damage_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed fixed-damage vocabulary."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-120-damage" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if ANNOUNCED_DAMAGE_EFFECT_FIELD in effect:
        return _announced_damage_capabilities(effect, target_schema, mechanics)
    if DAMAGE_DEATH_EXILE_FIELD in effect:
        if DAMAGE_DEATH_EXILE_MECHANIC not in mechanics or not damage_death_exile_scope_is_closed(effect, target_schema):
            return ()
        body = {key: value for key, value in effect.items() if key != DAMAGE_DEATH_EXILE_FIELD}
        base = fixed_damage_node_capabilities(effects=(body,), target_schema=target_schema, mechanic_ids=mechanics)
        return (*base, DAMAGE_DEATH_EXILE_CAPABILITY) if base else ()
    if (target_schema is None and set(effect) == {'op', 'source', 'target', 'amount'}
            and effect.get('op') == 'damage' and effect.get('source') == '$source'
            and effect.get('target') == '$event.block_counterpart' and _positive_int(effect.get('amount'))):
        return ('damage.amount.positive', 'damage.result.multitype_permanent',
                'damage.source.stack_current_or_lki', 'trigger.event.block_pair_counterpart')
    if (target_schema is None and isinstance(effect.get('target'),Mapping)
            and effect['target'].get('kind')=='target_controller_information'):
        from ..target_controller_information import controller_reference_index,TARGET_CONTROLLER_INFORMATION_CAPABILITY,TARGET_CONTROLLER_INFORMATION_MECHANIC
        try:index=controller_reference_index(effect['target'])
        except (TypeError,ValueError):return ()
        if (TARGET_CONTROLLER_INFORMATION_MECHANIC not in mechanics or index!=0
                or set(effect)!={'op','source','target','amount'} or effect['op']!='damage'
                or effect['source']!='$source' or not _positive_int(effect['amount'])):return ()
        return (TARGET_CONTROLLER_INFORMATION_CAPABILITY,'damage.amount.positive','damage.result.player_life','damage.source.stack_current_or_lki')
    fixed_set = _fixed_set_damage_capabilities(
        effect, target_schema, mechanics
    )
    if fixed_set is not None:
        return fixed_set
    if (target_schema is None and set(effect)=={'op','source','target','amount'}
            and effect.get('op')=='damage' and effect.get('source')=='$source'
            and effect.get('target')=='$controller' and _positive_int(effect.get('amount'))):
        return ('damage.amount.positive','damage.result.player_life','damage.source.stack_current_or_lki')
    if (
        target_schema is None
        and set(effect) == {"op", "source", "amount"}
        and effect.get("op") == "damage_each_opponent"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        return ("damage.amount.positive", "damage.result.player_life", "damage.source.stack_current_or_lki")
    return _fixed_direct_damage_capabilities(effect, target_schema, mechanics)


__all__ = ["fixed_damage_node_capabilities"]
