"""Source-spanned entry targeting for non-Aura linked reanimating enchantments."""

from dataclasses import replace

from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
from ..rules.linked_enchantment_entry import (
    LINKED_ENCHANTMENT_ENTRY_DEPENDENCIES, LINKED_ENCHANTMENT_ENTRY_MECHANIC,
    LINKED_ENCHANTMENT_ENTRY_TEMPLATE, linked_enchantment_entry_condition, linked_enchantment_entry_effect,
)
from ..rules.linked_graveyard_aura import linked_graveyard_aura_spell_target
from .dependency_gate import explicit_capabilities_gate
from .linked_graveyard_aura_nodes import _native_node_authority


def compile_linked_enchantment_entry_faces(record, faces, *, capability_registry, capability_profile):
    if record.faces or record.layout != "normal" or record.type_line.casefold() != "enchantment" or len(faces) != 1:
        return faces
    face, = faces
    lines = [line.strip().casefold().replace("’", "'") for line in face.oracle_text.splitlines()]
    linked_entry = (
        "when this enchantment enters, if it's on the battlefield, it becomes an aura with "
        f'"enchant creature put onto the battlefield with {record.name.casefold()}." '
        "put target creature card from a graveyard onto the battlefield under your control and attach "
        "this enchantment to it. when this enchantment leaves the battlefield, that creature's controller sacrifices it."
    )
    if len(lines) != 2 or lines[1] != linked_entry or len(face.nodes) != 2 or len(face.residuals) != 1:
        return faces
    timing, enters = face.nodes
    residual, = face.residuals
    if (
        not timing.exact or timing.event != "cast.permission" or enters.event != "permanent.enter.self"
        or enters.lowerable or not residual.material or residual.kind != "trigger_effect"
        or enters.residual_ids != (residual.residual_id,)
    ):
        return faces
    gate = explicit_capabilities_gate(
        LINKED_ENCHANTMENT_ENTRY_DEPENDENCIES,
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    authority = _native_node_authority(gate, capability_profile, enters.residual_ids)
    authority["mechanics"] = (LINKED_ENCHANTMENT_ENTRY_MECHANIC,)
    trigger = replace(
        enters, template_id=LINKED_ENCHANTMENT_ENTRY_TEMPLATE,
        effects=(linked_enchantment_entry_effect(),),
        target_schema=linked_graveyard_aura_spell_target(),
        event_condition=linked_enchantment_entry_condition(),
        runtime_coverage=(FIXED_PUBLIC_STATE_INTERVENING_COVERAGE, "intervening_condition"),
        **authority,
    )
    remaining = (
        replace(residual, kind="dependency_contract", blockers=gate.blockers,
                reason="Linked enchantment entry requires its Aura lifecycle and target authority"),
    ) if gate.blockers else ()
    return (replace(face, nodes=(timing, trigger), residuals=remaining),)
