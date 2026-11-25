from __future__ import annotations

"""One attached source designation governing a public destruction target."""

from collections.abc import Mapping

from ..entry_designations import (
    CHOSEN_COLOR_TARGET_PREDICATE, ENTRY_DESIGNATION_CAPABILITY, EntryDesignationKind, validate_designation,
)
from .linked_entry_opponent import entry_opponent_copy_form as linked_entry_copy_form
from ..replacement.immutable import thaw_value


CHOSEN_COLOR_TARGET_CAPABILITY = "target.public.source_chosen_color"
LINKED_ENTRY_COLOR_TARGET_FIELD = "linked_entry_color_target"
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


def linked_color_target_entry_programs(host, card):
    from ..card_program_faces import normalized_program_face_id, selected_face_id
    from ..entry_copy import copiable_oracle_id

    oracle_id = copiable_oracle_id(card)
    programs = tuple(
        program for program in host.semantics.programs_for_oracle(oracle_id, active_zone="battlefield")
        if is_closed_chosen_color_target_program(program)
    )
    if not programs:
        return ()
    record = host.card_db.by_oracle_id(oracle_id)
    face = selected_face_id(record, card)
    return tuple(sorted(
        program.key for program in programs
        if normalized_program_face_id(record, program) == face and host.semantic_program_is_current_trusted(program)
    ))


def color_target_entry_binding(card, color, program_keys):
    return {
        "schema_version": 1, "source_logical_object_id": card.logical_object_id,
        "face": card.active_face, "copy_form": linked_entry_copy_form(card),
        "program_keys": list(program_keys), "color": validate_designation(EntryDesignationKind.COLOR, color),
    }


def current_linked_target_color(card, *, program_key=None):
    raw = card.annotations.get(LINKED_ENTRY_COLOR_TARGET_FIELD)
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
