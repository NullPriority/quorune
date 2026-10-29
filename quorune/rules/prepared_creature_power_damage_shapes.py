"""Closure of fixed preparation followed by the same target's power damage."""
from typing import Any, Iterable, Mapping, Sequence

from .creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from .node_capability_shapes import (
    fixed_counter_placement_node_capabilities,
    fixed_target_characteristics_node_capabilities,
)

PREPARED_POWER_DAMAGE_MECHANIC = 'prepared-target-creature-power-damage'
PREPARED_POWER_DAMAGE_CAPABILITY = 'damage.creature_power.prepared_sequence'


def prepared_creature_power_damage_capabilities(
    *, effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Validate both constituents under their original target roles."""
    mechanics = tuple(mechanic_ids)
    if PREPARED_POWER_DAMAGE_MECHANIC not in mechanics or not 2 <= len(effects) <= 4:
        return ()
    last = effects[-1]
    if last.get('kind') != 'bite' or last.get('source') != '$target.0':
        return ()
    damage = fixed_creature_power_damage_node_capabilities(
        effects=(last,), target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not damage:
        return ()
    assert target_schema is not None
    first = dict(target_schema['groups'][0])
    if first.get('controller_relation') != 'you':
        return ()
    first.pop('id', None)
    preparation_schema = first
    preparation = effects[:-1]
    if any(effect.get('op') == 'place_counters' and effect.get('counter') != '+1/+1' for effect in preparation):
        return ()
    dependencies = fixed_target_characteristics_node_capabilities(
        effects=preparation, target_schema=preparation_schema, mechanic_ids=mechanics,
    )
    if not dependencies:
        dependencies = fixed_counter_placement_node_capabilities(
            effects=preparation, target_schema=preparation_schema, mechanic_ids=mechanics,
        )
    if not dependencies:
        return ()
    return tuple(sorted({PREPARED_POWER_DAMAGE_CAPABILITY, *damage, *dependencies}))
