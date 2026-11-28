from __future__ import annotations

from ..object_predicate import object_query_capabilities

"""Closed capability shapes for shared permanent predicates."""

from typing import Any, Mapping

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..object_predicate import ObjectQueryError, PermanentStatePredicateSpec


PUBLIC_PERMANENT_STATE_CAPABILITY = (
    "state_query.permanent.public_state_predicate"
)
TARGET_DAMAGE_HISTORY_CAPABILITY = (
    "target.permanent.damage_history_predicate"
)


def direct_permanent_target_schema_is_closed(
    target_schema: Mapping[str, Any] | None,
    *,
    allow_commander: bool = False,
) -> bool:
    """Return whether one direct permanent target schema is closed."""

    try:
        DirectPermanentTargetSpec.from_target_schema(
            target_schema,  # type: ignore[arg-type]
            allow_commander=allow_commander,
        )
    except (TypeError, ValueError):
        return False
    return True


# Compatibility for counter-family callers that predate the shared name.
fixed_counter_target_schema_is_closed = direct_permanent_target_schema_is_closed


def nonartifact_nonblack_target_runtime_dependencies(program) -> tuple[str, ...]:
    template = getattr(program, "provenance", {}).get("template_id")
    if type(template) is not str or "creature-non-artifact-non-b" not in template:
        return ()
    spec = DirectPermanentTargetSpec(types_any=("creature",), types_none=("artifact",), colors_none=("B",))
    owned_templates = {
        f"destroy-target-{spec.slug}-v2",
        f"destroy-target-{spec.slug}-regeneration-prohibited-v1",
        f"fixed-control-zone_object-{spec.slug}-v1",
    }
    if not owned_templates.intersection(template.split("+")):
        return ()
    from ..util import stable_json

    if stable_json(program.target_schema) != stable_json(spec.to_target_schema()):
        raise ValueError("The qualified creature target requires both exact type and color exclusions")
    return (*direct_target_predicate_capabilities(program.target_schema), "target.revalidate_resolution")


def controller_island_target_runtime_dependencies(program) -> tuple[str, ...]:
    template = getattr(program, "provenance", {}).get("template_id")
    if type(template) is not str:
        return ()
    from ..targets import CONTROLLER_ISLAND_TARGET_PREDICATE, ISLAND_COUNT_POWER_TARGET_PREDICATE
    expected = next((predicate for marker, predicate in (
        ("-controller-controls-island-", CONTROLLER_ISLAND_TARGET_PREDICATE),
        ("-power-at-most-controlled-islands-", ISLAND_COUNT_POWER_TARGET_PREDICATE),
    ) if marker in template), None)
    if expected is None:
        return ()
    schema = getattr(program, "target_schema", None)
    try:
        spec = DirectPermanentTargetSpec.from_target_schema(schema)
    except (TypeError, ValueError) as exc:
        raise ValueError("Controller-Island target requires its exact public creature role") from exc
    if spec.to_target_schema().get("predicate") != expected:
        raise ValueError("Island-qualified target must retain its exact live public predicate")
    return ("target.revalidate_resolution",)


def activated_tap_cost_target_runtime_dependencies(program) -> tuple[str, ...]:
    template = getattr(program, "provenance", {}).get("template_id")
    if type(template) is not str or "-with-activated-tap-cost-" not in template:
        return ()
    try:
        spec = DirectPermanentTargetSpec.from_target_schema(program.target_schema)
    except (TypeError, ValueError) as exc:
        raise ValueError("Tap-symbol targeting requires its exact public creature role") from exc
    if not spec.has_activated_tap_cost:
        raise ValueError("Tap-symbol targeting must retain its current ability predicate")
    return ("target.permanent.characteristic_predicate", "target.revalidate_resolution")


def direct_target_predicate_capabilities(
    target_schema: Mapping[str, Any],
    *, allow_commander: bool = False,
) -> tuple[str, ...]:
    """Return the characteristic and public-state owners for one target."""

    target_spec = DirectPermanentTargetSpec.from_target_schema(target_schema, allow_commander=allow_commander)
    return (
        *(("target.revalidate_resolution",) if (target_spec.controller_controls_island or target_spec.power_at_most_controlled_islands) else ()),
        *(("state_query.commander.permanent_designation",) if target_spec.commander else ()),
        *object_query_capabilities(target_spec.state_predicate),
        *(
            ("target.permanent.characteristic_predicate",)
            if target_spec.uses_compound_characteristics
            else ()
        ),
        *(
            (PUBLIC_PERMANENT_STATE_CAPABILITY,)
            if target_spec.uses_public_state
            else ()
        ),
        *(
            (TARGET_DAMAGE_HISTORY_CAPABILITY,)
            if target_spec.uses_damage_history
            else ()
        ),
    )


def public_state_query_capabilities(
    state_predicate: PermanentStatePredicateSpec | None,
) -> tuple[str, ...]:
    """Return the shared owner for a typed affected-set state predicate."""

    return (
        (PUBLIC_PERMANENT_STATE_CAPABILITY, *object_query_capabilities(state_predicate))
        if state_predicate is not None
        else ()
    )


def fixed_counter_target_set_state_capabilities(
    schema: Mapping[str, Any],
    *,
    types_any: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Validate the closed tapped-creature target-set state descriptor."""

    raw_state = schema.get("state_predicate")
    try:
        state_predicate = (
            PermanentStatePredicateSpec.from_dict(raw_state)
            if raw_state is not None
            else None
        )
    except ObjectQueryError:
        return None
    if state_predicate is None:
        return ()
    if (
        state_predicate.tapped is not True
        or state_predicate.entered_this_turn
        or state_predicate.counter_name is not None
        or types_any != ("creature",)
    ):
        return None
    return (PUBLIC_PERMANENT_STATE_CAPABILITY,)


__all__ = [
    "TARGET_DAMAGE_HISTORY_CAPABILITY",
    "direct_target_predicate_capabilities",
    "direct_permanent_target_schema_is_closed",
    "fixed_counter_target_schema_is_closed",
    "fixed_counter_target_set_state_capabilities",
    "public_state_query_capabilities",
]


def collect_fixed_permanent_target_runtime_requirements(program, required, blockers):
    """Validate the fixed creature qualification and target predicate owners."""
    for resolver, label in (
        (controller_island_target_runtime_dependencies, "controller_island_target"),
        (activated_tap_cost_target_runtime_dependencies, "activated_tap_cost_target"),
        (nonartifact_nonblack_target_runtime_dependencies, "qualified_creature_target"),
    ):
        try:
            required.update(resolver(program))
        except (TypeError, ValueError) as exc:
            blockers.add(f"runtime_{label}:invalid:{exc}")
