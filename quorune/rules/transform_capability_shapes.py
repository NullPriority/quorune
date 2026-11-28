from __future__ import annotations

"""Capability closure for source-pinned permanent transformation."""

from typing import Any, Iterable, Mapping, Sequence

from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_MECHANIC_ID
from .copied_flip_faces import COUNTER_END_STEP_FLIP_MECHANIC, FLIP_SOURCE_CAPABILITY, FLIP_SOURCE_OPERATION


PERMANENT_TRANSFORM_CAPABILITY_ID = "permanent.transform.face_change"
TRIGGERED_SOURCE_TRANSFORM_MECHANIC = "fixed-triggered-source-transform"
OPTIONAL_SOURCE_TRANSFORM_VERSION = 4


def optional_source_transform_child(wrapper, *, player, resolved=False):
    children = wrapper.get("effects")
    if (
        set(wrapper) != {"op", "player", "effects", "schema_version"}
        or wrapper["op"] != "offer_optional_effect"
        or wrapper["player"] != player
        or type(wrapper["schema_version"]) is not int
        or wrapper["schema_version"] != OPTIONAL_SOURCE_TRANSFORM_VERSION
        or not isinstance(children, (tuple, list))
        or len(children) != 1
        or not isinstance(children[0], Mapping)
    ):
        raise ValueError("Expected the versioned optional source transform")
    child = children[0]
    if set(child) != {"op", "card", "expected_transform_count"} or child["op"] != "transform":
        raise ValueError("Expected one original source transform")
    if resolved:
        ref, count = child["card"], child["expected_transform_count"]
        if (ref is not None and (type(ref) is not str or not ref)) or type(count) is not int or count < 0:
            raise ValueError("Expected the captured source and original transform count")
    elif child["card"] != "$source.zone_object" or child["expected_transform_count"] != "$source.transform_count":
        raise ValueError("Expected the original source and transform-count snapshot")
    return child


def source_transform_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {SPELL_HISTORY_TRANSFORM_MECHANIC_ID, "fixed-activated-source-transform",
             TRIGGERED_SOURCE_TRANSFORM_MECHANIC}.intersection(mechanics)
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if set(effect) != {
        "op",
        "card",
        "expected_transform_count",
    } or effect != {
        "op": "transform",
        "card": "$source.zone_object",
        "expected_transform_count": "$source.transform_count",
    }:
        return ()
    return (PERMANENT_TRANSFORM_CAPABILITY_ID,)


def _optional_counter_flip_effect():
    return {
        "op": "offer_optional_effect", "player": "$controller",
        "effects": [{"op": FLIP_SOURCE_OPERATION, "card": "$source.zone_object"}],
    }


def counter_end_step_flip_template(binding, body):
    from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec

    condition = binding.public_state_condition
    if ((binding.event.value, binding.active_zone) != ("step.begin", "battlefield")
            or binding.variant not in {"the end step", "each end step"}
            or not isinstance(condition, FixedPublicStateConditionSpec)
            or condition.kind is not FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST
            or body.casefold().rstrip(".") != "you may flip it"):
        return None
    return (
        "optional-counter-end-step-source-flip-v1", (_optional_counter_flip_effect(),), None,
        (COUNTER_END_STEP_FLIP_MECHANIC, "fixed-optional-effect-choice"),
    )


def counter_end_step_flip_node_capabilities(*, effects, target_schema, mechanics):
    from ..replacement.immutable import FrozenMap

    if COUNTER_END_STEP_FLIP_MECHANIC not in mechanics:
        return None
    if (target_schema is not None or len(effects) != 1
            or not isinstance(effects[0], Mapping)
            or FrozenMap(effects[0]) != FrozenMap(_optional_counter_flip_effect())):
        return ()
    return (FLIP_SOURCE_CAPABILITY, "effect.choice.optional_fixed")


def _counter_end_step_flip_runtime_dependencies(program):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..continuous_conditions import (
        FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
        FixedPublicStateConditionKind, FixedPublicStateConditionSpec,
    )
    from ..replacement.immutable import FrozenMap

    owned = counter_end_step_flip_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
    )
    try:
        schedule, leaf = program.event_condition["all"]
        spec = FixedPublicStateConditionSpec.from_dict(leaf["condition"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Optional source flip requires its original end-step counter condition") from exc
    if (not owned or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.cost_schema is not None or program.handlers
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
                    "intervening_condition"} <= set(program.coverage)
            or spec.kind is not FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST
            or FrozenMap(schedule) != FrozenMap({"all": [{"field": "step", "op": "eq", "value": "end_step"}]})
            or set(leaf) != {"field", "op", "value", "condition"}
            or leaf["field"] != FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD
            or leaf["op"] != "truthy" or leaf["value"] is not True):
        raise ValueError("Optional source flip requires its original end-step counter condition")
    return (*owned, "trigger.condition.fixed_public_state")


def is_closed_counter_end_step_flip_program(program):
    if COUNTER_END_STEP_FLIP_MECHANIC not in program.coverage:
        return False
    try:
        return set(_counter_end_step_flip_runtime_dependencies(program)) <= set(program.capability_dependencies)
    except ValueError:
        return False


def triggered_source_transform_runtime_dependencies(program) -> tuple[str, ...]:
    if COUNTER_END_STEP_FLIP_MECHANIC in program.coverage or any(
        isinstance(child, Mapping) and child.get("op") == FLIP_SOURCE_OPERATION
        for effect in program.effects if effect.get("op") == "offer_optional_effect"
        for child in effect.get("effects", ())
    ):
        return _counter_end_step_flip_runtime_dependencies(program)
    if TRIGGERED_SOURCE_TRANSFORM_MECHANIC not in program.coverage:
        return ()
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE
    from .fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities,fixed_optional_mana_payment_node_capabilities

    if program.active_zone != "battlefield" or not {
        "fixed-typed-event-effect-trigger",
        CURRENT_ABILITY_FRAGMENT_COVERAGE,
        SPELL_HISTORY_TRANSFORM_COVERAGE,
    }.issubset(program.coverage):
        raise ValueError("Triggered source transformation requires its source and transform-count snapshots")
    effects = program.effects
    optional = len(effects) == 1 and effects[0].get("op") in {"offer_optional_effect","offer_optional_mana_payment"}
    required = source_transform_node_capabilities(
        effects=effects[0].get("effects", ()) if optional else effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Triggered source transformation requires one closed source instruction")
    if optional:
        resolve=(fixed_optional_mana_payment_node_capabilities if effects[0]['op']=='offer_optional_mana_payment'
            else fixed_optional_effect_node_capabilities)
        wrapper = resolve(
            effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if not wrapper:
            raise ValueError("Optional source transformation requires its closed choice")
        return wrapper
    return required


__all__ = [
    "PERMANENT_TRANSFORM_CAPABILITY_ID",
    "TRIGGERED_SOURCE_TRANSFORM_MECHANIC",
    "triggered_source_transform_runtime_dependencies",
    "source_transform_node_capabilities",
]
