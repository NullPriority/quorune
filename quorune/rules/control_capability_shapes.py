from __future__ import annotations

"""Closed ownership for resolution-created control instructions."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.fixed_control_templates import FIXED_CONTROL_CAPABILITY, FIXED_CONTROL_MECHANIC
from ..continuous_effect_model import ContinuousEffectDuration
from ..control_effects import control_set_query_is_closed
from ..control_exchange_model import (
    PERMANENT_CONTROL_EXCHANGE_CAPABILITY,
    PERMANENT_CONTROL_EXCHANGE_MECHANIC, PERMANENT_CONTROL_EXCHANGE_OPERATION,
    PermanentControlExchange, decode_permanent_control_exchange,
)
from ..entry_control_exchange_model import entry_exchange_event_condition
from ..replacement.immutable import thaw_value
from ..util import stable_json
from ..object_predicate import ObjectQuerySpec
from ..recipient_control_model import (
    TWO_CONTROLLER_CREATURE_TRANSFER_FIELD, two_controller_creature_transfer_target,
    validate_two_controller_creature_transfer,
)
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities,
)


def control_covered_mechanics(supplied: set[str]) -> set[str]:
    covered = {FIXED_CONTROL_MECHANIC, "cr-611-continuous-effects"} if FIXED_CONTROL_CAPABILITY in supplied else set()
    if PERMANENT_CONTROL_EXCHANGE_CAPABILITY in supplied:
        covered.add(PERMANENT_CONTROL_EXCHANGE_MECHANIC)
    return covered


def _exchange_dependencies(effect, schema):
    scope, _references = decode_permanent_control_exchange(effect)
    if stable_json(thaw_value(schema)) != stable_json(scope.target_schema()):
        raise ValueError("Permanent control exchange requires its exact target groups")
    return scope.capability_dependencies


def permanent_control_exchange_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = {PERMANENT_CONTROL_EXCHANGE_MECHANIC, "cr-115-targets", "cr-611-continuous-effects"}
    if len(effects) != 1 or not mechanics <= set(mechanic_ids):
        return ()
    try:
        return _exchange_dependencies(effects[0], target_schema)
    except (TypeError, ValueError):
        return ()


def permanent_control_exchange_runtime_dependencies(program):
    exchanges = tuple(
        effect for effect in program.effects if effect.get("op") == PERMANENT_CONTROL_EXCHANGE_OPERATION
    )
    if not exchanges:
        return ()
    if len(program.effects) != 1:
        raise ValueError("Permanent control exchange requires one complete instruction")
    if exchanges[0].get("scope") == PermanentControlExchange.ENTRY_SHARED_TYPE.value and (
            (program.event, program.active_zone) != ("permanent.enter", "battlefield")
            or thaw_value(program.event_condition) != entry_exchange_event_condition()):
        raise ValueError("Entry exchange requires its original permanent entry subscription")
    return _exchange_dependencies(exchanges[0], program.target_schema)


def _two_controller_creature_transfer_dependencies(effects, target_schema, mechanics):
    if (len(effects) != 1 or not {"cr-115-targets", "tap-and-untap", "haste"} <= mechanics
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema != two_controller_creature_transfer_target()):
        return ()
    try:
        validate_two_controller_creature_transfer(effects[0])
    except (TypeError, ValueError):
        return ()
    return (FIXED_CONTROL_CAPABILITY, "target.revalidate_resolution", "permanent.untap.effect",
            "continuous.resolution.fixed_characteristics_until_end_of_turn")


def two_controller_creature_transfer_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(TWO_CONTROLLER_CREATURE_TRANSFER_FIELD in effect
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    result = fixed_control_node_capabilities(effects=program.effects, target_schema=program.target_schema,
                                             mechanic_ids=program.coverage)
    if (not result or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Two-controller creature transfer requires the original resolving spell and opponent")
    return result


def resolution_control_node_capabilities(*,effects,target_schema,mechanic_ids):
    """Keep strictly shaped control effects on the shared control owner."""
    exchange = permanent_control_exchange_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    if exchange:
        return exchange
    owned=fixed_control_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if owned:return owned
    from .recipient_control_shapes import recipient_control_node_capabilities
    from .owner_control_shapes import owner_control_node_capabilities
    from .directional_control import directional_control_node_capabilities
    return (recipient_control_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
        or owner_control_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
        or directional_control_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids))


def fixed_control_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = set(mechanic_ids)
    if not {FIXED_CONTROL_MECHANIC, "cr-611-continuous-effects"}.issubset(mechanics) or not effects:
        return ()
    from .attached_temporary_control import (
        ATTACHED_TEMPORARY_CONTROL_VERSION, attached_temporary_control_node_capabilities,
    )

    if len(effects) == 1 and ATTACHED_TEMPORARY_CONTROL_VERSION in effects[0]:
        return attached_temporary_control_node_capabilities(effects=effects, target_schema=target_schema)
    if len(effects) == 1 and isinstance(effects[0], Mapping) and TWO_CONTROLLER_CREATURE_TRANSFER_FIELD in effects[0]:
        return _two_controller_creature_transfer_dependencies(effects, target_schema, mechanics)
    dependencies = {FIXED_CONTROL_CAPABILITY}
    if target_schema is None:
        if len(effects) != 1:
            return ()
        effect = effects[0]
        if set(effect) != {"op", "predicate", "controller", "duration", "source", "steps"}:
            return ()
        if effect["op"] != "gain_control_set" or effect["controller"] != "$controller" or effect["source"] != "$source":
            return ()
        try:
            query = ObjectQuerySpec.from_dict(effect["predicate"])
            duration = ContinuousEffectDuration(effect["duration"])
        except (TypeError, ValueError):
            return ()
        if duration not in {duration.UNTIL_END_OF_TURN, duration.ZONE_OBJECT}:
            return ()
        steps = effect["steps"]
        if not isinstance(steps, (tuple, list)) or tuple(steps) not in {
            ("gain_control",), ("untap", "gain_control"), ("gain_control", "untap"),
            ("gain_control", "haste"), ("untap", "gain_control", "haste"),
            ("gain_control", "untap", "haste"),
        } or not control_set_query_is_closed(query, actor="$controller"):
            return ()
        if "untap" in steps:
            if "tap-and-untap" not in mechanics:
                return ()
            dependencies.add("permanent.untap.effect")
        if "haste" in steps:
            if "haste" not in mechanics or duration is not duration.UNTIL_END_OF_TURN:
                return ()
            dependencies.add("continuous.resolution.fixed_characteristics_until_end_of_turn")
        return tuple(sorted(dependencies))
    if "cr-115-targets" not in mechanics or not direct_permanent_target_schema_is_closed(target_schema):
        return ()
    if len(effects) == 2:
        if dict(effects[0]) != {"op": "untap", "card": "$target.0"} or "tap-and-untap" not in mechanics:
            return ()
        dependencies.add("permanent.untap.effect")
    elif len(effects) != 1:
        return ()
    effect = effects[-1]
    try:
        duration = ContinuousEffectDuration(effect.get("duration"))
    except (TypeError, ValueError):
        return ()
    expected = {"op", "card", "controller", "duration", "source"}
    if duration.source_bound:
        expected.add("duration_source")
        if effect.get("duration_source") != "$source.zone_object" or len(effects) != 1:
            return ()
    elif duration not in {duration.UNTIL_END_OF_TURN, duration.ZONE_OBJECT}:
        return ()
    if (set(effect) != expected or effect.get("op") != "gain_control"
            or effect.get("card") != "$target.0" or effect.get("controller") != "$controller"
            or effect.get("source") != "$source"):
        return ()
    dependencies.add("target.revalidate_resolution")
    dependencies.update(direct_target_predicate_capabilities(target_schema))
    return tuple(sorted(dependencies))
