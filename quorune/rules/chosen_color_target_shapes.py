from __future__ import annotations

"""Bind a current entry color to the same-face programs that can consume it."""

from collections.abc import Mapping

from ..entry_designations import (
    CHOSEN_COLOR_TARGET_PREDICATE, ENTRY_DESIGNATION_CAPABILITY, EntryDesignationKind, validate_designation,
    LINKED_COLOR_PROTECTION_CAPABILITIES, LINKED_COLOR_PROTECTION_HANDLER_ID, LINKED_COLOR_PROTECTION_TEMPLATE,
)
from .linked_entry_opponent import entry_opponent_copy_form as linked_entry_copy_form
from ..replacement.immutable import thaw_value


CHOSEN_COLOR_TARGET_CAPABILITY = "target.public.source_chosen_color"
LINKED_ENTRY_COLOR_FIELD = "linked_entry_color_target"
_REQUIRED = (
    CHOSEN_COLOR_TARGET_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY,
    "permanent.destroy.effect", "target.revalidate_resolution",
)
TARGET_SCHEMA = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "predicate": CHOSEN_COLOR_TARGET_PREDICATE,
}


def uses_source_color_target(value):
    if isinstance(value, Mapping):
        return value.get("predicate") == CHOSEN_COLOR_TARGET_PREDICATE or any(
            uses_source_color_target(child) for child in value.values()
        )
    return isinstance(value, (tuple, list)) and any(uses_source_color_target(child) for child in value)


def _require_color_target(effects, schema):
    if (not isinstance(schema, Mapping) or type(schema.get("count")) is not int
            or thaw_value(schema) != TARGET_SCHEMA
            or thaw_value(effects) != [{"op": "destroy", "card": "$target.0"}]):
        raise ValueError("The chosen color requires its exact compulsory permanent destruction")


def chosen_color_target_node_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    if not {"destroy", "cr-115-targets"}.issubset(mechanics):
        return ()
    try:
        _require_color_target(effects, target_schema)
    except (TypeError, ValueError):
        return ()
    return _REQUIRED


def chosen_color_target_runtime_dependencies(program):
    if not uses_source_color_target(program.target_schema):
        return ()
    _require_color_target(program.effects, program.target_schema)
    if program.event != "activate" or program.active_zone != "battlefield":
        raise ValueError("Chosen-color destruction requires a battlefield activation")
    return _REQUIRED


def is_closed_chosen_color_target_program(program):
    try:
        required = chosen_color_target_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def linked_color_protection_runtime_dependencies(program):
    marked = program.provenance.get("template_id") == LINKED_COLOR_PROTECTION_TEMPLATE
    if not marked and not any(extra.get("handler_id") == LINKED_COLOR_PROTECTION_HANDLER_ID
                              for extra in program.handlers):
        return ()
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..semantic_runtime.chosen_characteristics import linked_color_protection_descriptor

    if (
        not marked or (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
        or len(program.handlers) != 1 or dict(program.handlers[0]) != linked_color_protection_descriptor()
        or program.effects or program.target_schema is not None or program.cost_schema is not None
        or program.event_condition is not None or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Linked color protection requires its current self-protection component")
    return LINKED_COLOR_PROTECTION_CAPABILITIES


def _uses_linked_entry_color(program):
    from ..characteristic_fragments import CHOSEN_COLOR_DEFINITION_CAPABILITY, CHOSEN_COLOR_DEFINITION_TEMPLATE
    from ..triggered_mana_model import (
        SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY, SOURCE_CHOSEN_TRIGGERED_MANA_TEMPLATE,
        fixed_triggered_mana_runtime_dependencies,
    )

    if program.provenance.get("template_id") == SOURCE_CHOSEN_TRIGGERED_MANA_TEMPLATE:
        try:
            required = fixed_triggered_mana_runtime_dependencies(program)
        except (ValueError, TypeError, KeyError):
            return False
        return (required == (SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY,)
                and SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY in program.capability_dependencies)
    if program.provenance.get("template_id") == CHOSEN_COLOR_DEFINITION_TEMPLATE:
        from .live_modifier_quantities import validate_chosen_color_definition_program

        try:
            validate_chosen_color_definition_program(program, set(program.capability_dependencies))
        except (ValueError, TypeError):
            return False
        return CHOSEN_COLOR_DEFINITION_CAPABILITY in program.capability_dependencies
    if is_closed_chosen_color_target_program(program):
        return True
    try:
        required = linked_color_protection_runtime_dependencies(program)
    except (ValueError, TypeError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def linked_color_entry_programs(host, card):
    from ..card_program_faces import normalized_program_face_id, selected_face_id
    from ..entry_copy import copiable_oracle_id

    oracle_id = copiable_oracle_id(card)
    programs = tuple(
        program for program in host.semantics.programs_for_oracle(oracle_id)
        if _uses_linked_entry_color(program)
    )
    if not programs:
        return ()
    record = host.card_db.by_oracle_id(oracle_id)
    face = selected_face_id(record, card)
    return tuple(sorted(
        program.key for program in programs
        if normalized_program_face_id(record, program) == face and host.semantic_program_is_current_trusted(program)
    ))


def linked_color_entry_binding(card, color, program_keys):
    return {
        "schema_version": 1, "source_logical_object_id": card.logical_object_id,
        "face": card.active_face, "copy_form": linked_entry_copy_form(card),
        "program_keys": list(program_keys), "color": validate_designation(EntryDesignationKind.COLOR, color),
    }


def current_linked_entry_color(card, *, program_key=None):
    raw = card.annotations.get(LINKED_ENTRY_COLOR_FIELD)
    fields = {"schema_version", "source_logical_object_id", "face", "copy_form", "program_keys", "color"}
    if (
        not isinstance(raw, Mapping) or set(raw) != fields
        or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
        or raw["source_logical_object_id"] != card.logical_object_id or raw["face"] != card.active_face
        or not isinstance(raw["program_keys"], (tuple, list)) or not raw["program_keys"]
        or any(type(key) is not str or not key for key in raw["program_keys"])
        or program_key is not None and program_key not in raw["program_keys"]
        or raw["copy_form"] != linked_entry_copy_form(card)
    ):
        return None
    return validate_designation(EntryDesignationKind.COLOR, raw["color"])
