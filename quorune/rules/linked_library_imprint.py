"""Infer exact capabilities for both independent linked library imprint stages."""

from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY
from ..linked_library_imprint_model import (
    LIBRARY_IMPRINT_CAPABILITY, LIBRARY_IMPRINT_MECHANIC, LIBRARY_IMPRINT_PHASES,
    library_imprint_phase, uses_library_imprint_death, uses_library_imprint_selection,
    validate_library_imprint_selection, library_imprint_source, linked_artifact_library_choice,
    uses_linked_artifact_library_choice, uses_linked_artifact_library_instruction,
    validate_linked_artifact_library_instruction,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


_LINKED_REQUIREMENTS = (
    LIBRARY_IMPRINT_CAPABILITY, LINKED_ABILITY_EXILE_CAPABILITY,
    "zone.change.destination_replacement",
)


def uses_library_imprint(effect):
    return uses_library_imprint_selection(effect) or uses_library_imprint_death(effect)


def linked_library_imprint_shape_capabilities(effects, target_schema, mechanics):
    if any(uses_linked_artifact_library_choice(effect) for effect in effects):
        return linked_artifact_library_node_capabilities(effects=effects, target_schema=target_schema)
    if LIBRARY_IMPRINT_MECHANIC in mechanics or any(uses_library_imprint(effect) for effect in effects):
        return library_imprint_node_capabilities(effects=effects, target_schema=target_schema)
    return None


def library_imprint_node_capabilities(*, effects, target_schema):
    if target_schema is not None:
        return ()
    try:
        if len(effects) == 1 and uses_library_imprint_selection(effects[0]):
            validate_library_imprint_selection(effects[0], player="$controller")
            return (*_LINKED_REQUIREMENTS, "library.select.fixed_controller")
        if tuple(library_imprint_phase(effect) for effect in effects) == LIBRARY_IMPRINT_PHASES:
            return (*_LINKED_REQUIREMENTS, "zone.linked_exile_return.fixed")
    except (TypeError, ValueError):
        return ()
    return ()


def library_imprint_program_dependencies(program):
    if any(uses_linked_artifact_library_instruction(effect)
           for effect in nested_effects((program.effects, program.handlers))):
        return linked_artifact_library_program_dependencies(program)
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_library_imprint(effect))
    if not marked and LIBRARY_IMPRINT_CAPABILITY not in program.capability_dependencies:
        return ()
    if tuple(program.effects) != marked or not marked or program.active_zone != "battlefield":
        raise ValueError("Linked library imprint requires its original top-level battlefield trigger")
    expected = "permanent.enter.self" if uses_library_imprint_selection(marked[0]) else "creature.dies.self"
    owned = library_imprint_node_capabilities(effects=marked, target_schema=program.target_schema)
    if program.event != expected or not owned:
        raise ValueError("Linked library imprint split or changed its original event or whole result")
    return owned


def linked_artifact_library_node_capabilities(*, effects, target_schema):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        child = linked_artifact_library_choice(effects[0], player="$controller")
        _, returning = validate_linked_artifact_library_instruction(child)
    except (TypeError, ValueError, KeyError):
        return ()
    return (
        LINKED_ABILITY_EXILE_CAPABILITY, "zone.change.destination_replacement",
        "effect.choice.optional_fixed",
        "zone.linked_exile_return.fixed" if returning else "library.search.fixed_counted",
    )


def linked_artifact_library_program_dependencies(program):
    owned = linked_artifact_library_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not owned or program.active_zone != "battlefield" or program.handlers
            or program.cost_schema is not None or program.event_condition is not None
            or not program.ability_id.startswith("trigger:")):
        raise ValueError("Linked artifact library instructions require the original battlefield trigger")
    child = linked_artifact_library_choice(program.effects[0], player="$controller")
    _, returning = validate_linked_artifact_library_instruction(child)
    if program.event != ("creature.dies.self" if returning else "permanent.enter.self"):
        raise ValueError("Linked artifact library instructions require their original entry or death")
    return owned


def linked_artifact_library_source(host, item, effect, *, actor):
    pair, _ = validate_linked_artifact_library_instruction(effect)
    source = library_imprint_source(item, actor=actor)
    program = host.semantics.get(item.semantic_key)
    if (program is None or not linked_artifact_library_program_dependencies(program)
            or thaw_value(effect) != thaw_value(linked_artifact_library_choice(program.effects[0], player="$controller"))):
        raise ValueError("Linked artifact library instruction lost its original exact program")
    return source, pair


def require_linked_artifact_library_search(host, item, effect, *, actor):
    if not uses_linked_artifact_library_instruction(effect):
        return
    from ..errors import GameRuleError

    try:
        linked_artifact_library_source(host, item, effect, actor=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
