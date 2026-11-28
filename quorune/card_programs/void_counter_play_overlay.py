from __future__ import annotations

"""Select current typed void-counter views for an exact pinned source."""

from dataclasses import replace

from ..replacement.immutable import thaw_value
from ..rules.void_counter_play import is_closed_void_counter_play_program
from .current_view_equivalence import _catalog_preserves_cost, _same_current_source


_LEGACY_SCHEMA = {
    "zones": ["exile"], "categories": ["card"], "owner_relation": "opponent",
    "predicate": "void_counter", "count": 1,
}
_LEGACY_EFFECTS = [{"op": "grant_play_without_mana_cost", "card": "$target.0", "source": "$source"}]


def reviewed_void_counter_play_matches(record, current, old):
    if (
        current.key != old.key or current.ability_id != old.ability_id
        or not _same_current_source(record, current, old, compare_targets=False)
        or current.semantic_schema_version != old.semantic_schema_version
        or not str(current.provenance.get("authored_by", "")).startswith("oracle-ir-v")
        or current.event_condition != old.event_condition
        or current.trigger_limit != old.trigger_limit or current.requires_arbiter
        or thaw_value(old.target_schema) != _LEGACY_SCHEMA
        or list(thaw_value(old.effects)) != _LEGACY_EFFECTS
        or len(old.handlers) != 1 or not is_closed_void_counter_play_program(current)
    ):
        return False
    legacy = thaw_value(old.handlers[0])
    if legacy.get("ability", {}).get("target_schema") != _LEGACY_SCHEMA:
        return False
    legacy["ability"]["target_schema"] = None
    return _catalog_preserves_cost(current, replace(old, handlers=[legacy]))


_REPLACEMENT_CAPABILITY = "zone.change.destination_replacement"
_REPLACEMENT_DESCRIPTOR = {
    "handler_id": "replacement.zone.destination.v1",
    "schema_version": 1, "event": "zone.change",
    "condition": {"destination": "graveyard", "object_kind": "card", "owner_relation": "opponent"},
    "destination": "exile", "counters": {"void": 1},
}


def _void_counter_replacement_view(program):
    return (
        (program.event, program.active_zone) == ("zone.change", "battlefield")
        and not program.effects and not program.requires_arbiter
        and program.target_schema is None and program.cost_schema is None
        and program.event_condition is None and program.trigger_limit is None
        and len(program.handlers) == 1
        and type(program.handlers[0].get("schema_version")) is int
        and thaw_value(program.handlers[0]) == _REPLACEMENT_DESCRIPTOR
    )


def shadowed_reviewed_void_counter_replacement_keys(
    record, generated, reviewed, *, capability_registry, capability_profile,
):
    if capability_registry is None:
        return set()
    from .binding import bind_semantic_program_runtime

    candidates = tuple(current for current in generated if _void_counter_replacement_view(current)
                       and _REPLACEMENT_CAPABILITY in current.capability_dependencies
                       and str(current.provenance.get("authored_by", "")).startswith("oracle-ir-v"))
    return {
        old.key for old in reviewed if _void_counter_replacement_view(old)
        and sum(
            _same_current_source(record, current, old)
            and current.semantic_schema_version == old.semantic_schema_version
            and bind_semantic_program_runtime(current, capability_registry=capability_registry,
                                              profile=capability_profile)["strict"]
            for current in candidates
        ) == 1
    }
