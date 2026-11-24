"""Capability closure for the two ordinary battlefield redirect frames."""

from __future__ import annotations

from ..replacement.immutable import thaw_value
from .source_directed_redirect_model import (
    SOURCE_DIRECTED_REDIRECT_CAPABILITY, is_source_directed_redirect, source_directed_redirect_from_effect,
)
from .effect_shape_walkers import nested_effects


def source_directed_redirect_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or "cr-115-targets" not in mechanic_ids:
        return ()
    try:
        spec = source_directed_redirect_from_effect(thaw_value(effects[0]))
    except (TypeError, ValueError):
        return ()
    if thaw_value(target_schema) != spec.target_schema:
        return ()
    return (SOURCE_DIRECTED_REDIRECT_CAPABILITY, "target.revalidate_resolution", "trigger.event.normalized_target_announcement")


def source_directed_redirect_runtime_dependencies(program):
    if not any(is_source_directed_redirect(effect) for effect in nested_effects(program.effects)):
        return ()
    required = source_directed_redirect_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required or program.active_zone != "battlefield" or program.event_condition is not None:
        raise ValueError("A source-directed target change requires its closed battlefield instruction")
    optional = program.effects[0]["optional"]
    if optional:
        if program.event != "permanent.enter.self" or program.cost_schema is not None or program.handlers:
            raise ValueError("Optional source redirection requires its complete self-entry trigger")
    elif program.event != "activate" or program.cost_schema is None:
        raise ValueError("Required source redirection needs its paid battlefield activation")
    return required


def source_directed_redirect_program_is_closed(program):
    try:
        required = source_directed_redirect_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
