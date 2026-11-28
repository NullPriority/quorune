"""Retire only the reviewed pair superseded by a complete linked Aura program."""

from ..replacement.immutable import FrozenMap
from ..rules.linked_graveyard_aura import (
    LINKED_GRAVEYARD_AURA_ENTRY_TEMPLATE, LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE,
    linked_graveyard_aura_entry_effect, linked_graveyard_aura_prepare_effect,
    linked_graveyard_aura_spell_handler, linked_graveyard_aura_spell_target,
)


_LEGACY_LINK = "animate_dead_creature"


def _reviewed_component_is_equivalent(old, fresh):
    if (old.oracle_id != fresh.oracle_id or old.trust_level != "trusted" or old.requires_arbiter
            or old.provenance.get("review_status") != "reviewed"
            or not fresh.provenance.get("source_oracle_hash") or not fresh.provenance.get("source_rulings_hash")
            or any(old.provenance.get(key) != fresh.provenance[key]
                   for key in ("source_oracle_hash", "source_rulings_hash"))
            or any(getattr(old, field) != getattr(fresh, field) for field in (
                "active_zone", "event", "destination", "cost_schema", "event_condition", "trigger_limit",
                "semantic_schema_version",
            ))):
        return False
    effects = tuple(map(FrozenMap, old.effects))
    if fresh.provenance.get("template_id") == LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE:
        return (
            old.ability_id == "spell:front"
            and tuple(map(FrozenMap, old.handlers)) == (FrozenMap(linked_graveyard_aura_spell_handler(link=_LEGACY_LINK)),)
            and FrozenMap(old.target_schema or {}) == FrozenMap(linked_graveyard_aura_spell_target())
            and effects == (FrozenMap(linked_graveyard_aura_prepare_effect()),)
        )
    return (
        old.ability_id == "trigger:enter" and old.target_schema is None and not old.handlers
        and effects == (FrozenMap(linked_graveyard_aura_entry_effect(
            link=_LEGACY_LINK, event_code="animate_dead.reanimate",
        )),)
    )


def shadowed_reviewed_linked_graveyard_aura_keys(generated, reviewed, *, capability_registry, capability_profile):
    from .binding import bind_semantic_program_runtime

    if capability_registry is None:
        return set()
    expected = {LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE, LINKED_GRAVEYARD_AURA_ENTRY_TEMPLATE}
    candidates = [program for program in generated if program.provenance.get("template_id") in expected]
    if (len(candidates) != 2 or {row.provenance.get("template_id") for row in candidates} != expected
            or any(row.trust_level != "trusted" or not bind_semantic_program_runtime(
                row, capability_registry=capability_registry, profile=capability_profile,
            )["strict"] for row in candidates)):
        return set()
    return {old.key for old in reviewed if any(_reviewed_component_is_equivalent(old, fresh) for fresh in candidates)}
