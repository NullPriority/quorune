"""Compile a complete linked graveyard Aura alongside its ordinary static nodes."""

from dataclasses import replace

from ..rules.linked_graveyard_aura import (
    ENTRY_DEPENDENCIES, LINKED_GRAVEYARD_AURA_ENTRY_TEMPLATE, LINKED_GRAVEYARD_AURA_MECHANIC,
    LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE, SPELL_DEPENDENCIES, linked_graveyard_aura_entry_effect,
    linked_graveyard_aura_prepare_effect, linked_graveyard_aura_spell_handler, linked_graveyard_aura_spell_target,
)
from .dependency_gate import explicit_capabilities_gate


_ENCHANT = "enchant creature card in a graveyard"
_ENTER = (
    'when this aura enters, if it\'s on the battlefield, it loses "enchant creature card in a graveyard" '
    'and gains "enchant creature put onto the battlefield with this aura." '
    "return enchanted creature card to the battlefield under your control and attach this aura to it. "
    "when this aura leaves the battlefield, that creature's controller sacrifices it."
)
_MODIFIER = "enchanted creature gets -1/-0."


def _native_node_authority(gate, profile, residual_ids):
    closure = gate.closure
    return {
        "lowerable": True, "exact": not gate.blockers,
        "mechanics": (LINKED_GRAVEYARD_AURA_MECHANIC,),
        "capability_dependencies": gate.capabilities,
        "capability_closure": closure.reachable if closure is not None else (),
        "capability_profile": profile if closure is not None else None,
        "capability_fingerprint": closure.fingerprint if closure is not None else None,
        "residual_ids": residual_ids if gate.blockers else (),
    }


def compile_linked_graveyard_aura_faces(record, faces, *, capability_registry, capability_profile):
    if record.faces or record.layout != "normal" or record.type_line.casefold() != "enchantment — aura" or len(faces) != 1:
        return faces
    face, = faces
    printed = tuple(line.strip().casefold().replace("’", "'") for line in face.oracle_text.splitlines())
    if printed != (_ENCHANT, _ENTER, _MODIFIER) or len(face.nodes) != 3 or len(face.residuals) != 1:
        return faces
    enchant, enters, modifier = face.nodes
    residual, = face.residuals
    if (not enchant.exact or not modifier.exact or enters.event != "permanent.enter.self"
            or enters.lowerable or not residual.material or residual.kind != "trigger_effect"
            or enters.residual_ids != (residual.residual_id,)):
        return faces
    options = {"capability_registry": capability_registry, "capability_profile": capability_profile}
    spell = explicit_capabilities_gate(SPELL_DEPENDENCIES, **options)
    entry = explicit_capabilities_gate(ENTRY_DEPENDENCIES, **options)
    blockers = tuple(sorted(set(spell.blockers) | set(entry.blockers)))
    cast = replace(enchant, node_id=f"{enchant.node_id}:linked-cast", kind="spell_ability",
        active_zone="stack", event="resolve", template_id=LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE,
        effects=(linked_graveyard_aura_prepare_effect(),), handlers=(linked_graveyard_aura_spell_handler(),),
        target_schema=linked_graveyard_aura_spell_target(), runtime_coverage=(),
        **_native_node_authority(spell, capability_profile, enters.residual_ids))
    trigger = replace(enters, template_id=LINKED_GRAVEYARD_AURA_ENTRY_TEMPLATE,
        effects=(linked_graveyard_aura_entry_effect(),),
        **_native_node_authority(entry, capability_profile, enters.residual_ids))
    remaining = (replace(residual, kind="dependency_contract", blockers=blockers,
        reason="Linked graveyard Aura requires its complete attachment and return authority"),) if blockers else ()
    return (replace(face, nodes=(enchant, cast, trigger, modifier), residuals=remaining),)
