"""Closure of fixed preparation followed by the same target's power damage."""
from typing import Any, Iterable, Mapping, Sequence

from .creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from .node_capability_shapes import (
    fixed_counter_placement_node_capabilities,
    fixed_target_characteristics_node_capabilities,
)

PREPARED_POWER_DAMAGE_MECHANIC = 'prepared-target-creature-power-damage'
PREPARED_POWER_DAMAGE_CAPABILITY = 'damage.creature_power.prepared_sequence'
UNTAPPED_CONTROLLER_DAMAGE_TEMPLATE = "untapped-target-then-controller-power-damage-v1"


def untapped_controller_damage_effects():
    from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_OPERATION
    from ..target_controller_reference import target_controller_reference

    return (
        {"op": "tap", "card": "$target.0"},
        {"op": CREATURE_POWER_DAMAGE_OPERATION, "kind": "bite", "source": "$target.0",
         "target": target_controller_reference(), "target_must_be_creature": False, "source_lki": None},
    )


def _untapped_controller_damage_capabilities(effects, target_schema, mechanics):
    from ..compiler.direct_target import direct_permanent_target_spec
    from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_CAPABILITY, CREATURE_POWER_DAMAGE_MECHANIC
    from ..replacement.immutable import thaw_value
    from ..target_controller_reference import TARGET_CONTROLLER_INFORMATION_CAPABILITY, TARGET_CONTROLLER_INFORMATION_MECHANIC
    from ..util import stable_json
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    expected = direct_permanent_target_spec("target untapped creature").to_target_schema()
    if (not {PREPARED_POWER_DAMAGE_MECHANIC, CREATURE_POWER_DAMAGE_MECHANIC,
             TARGET_CONTROLLER_INFORMATION_MECHANIC, "tap-and-untap", "cr-115-targets", "cr-120-damage"} <= set(mechanics)
            or stable_json(thaw_value(target_schema)) != stable_json(expected)
            or stable_json(thaw_value(effects)) != stable_json(untapped_controller_damage_effects())):
        return ()
    return (
        PREPARED_POWER_DAMAGE_CAPABILITY, CREATURE_POWER_DAMAGE_CAPABILITY,
        TARGET_CONTROLLER_INFORMATION_CAPABILITY, "permanent.tap.effect", "damage.amount.positive",
        "damage.result.player_life", "target.revalidate_resolution", *direct_target_predicate_capabilities(expected),
    )


def untapped_controller_damage_runtime_dependencies(program):
    if getattr(program, "provenance", {}).get("template_id") != UNTAPPED_CONTROLLER_DAMAGE_TEMPLATE:
        return ()
    required = _untapped_controller_damage_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers
            or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Controller power damage requires the original untapped creature and ordered tap")
    return required


def prepared_creature_power_damage_capabilities(
    *, effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Validate both constituents under their original target roles."""
    mechanics = tuple(mechanic_ids)
    if len(effects) == 2 and effects[0].get("op") == "tap":
        return _untapped_controller_damage_capabilities(effects, target_schema, mechanics)
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
