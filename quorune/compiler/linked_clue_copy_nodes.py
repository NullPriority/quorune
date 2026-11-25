"""Keep the attack producer and the Clue sacrifice result in one source pair."""

from dataclasses import replace

from ..rules.linked_clue_copy import (
    LINKED_CLUE_COPY_DEPENDENCIES,
    linked_clue_attack_condition,
    linked_clue_attack_effects,
    linked_clue_attack_target,
    linked_clue_choice_effect,
    linked_clue_sacrifice_condition,
)
from .dependency_gate import explicit_capabilities_gate
from .linked_clue_copy_templates import linked_clue_copy_source


def _compile_face_pair(face, *, material_line_for, capability_registry, capability_profile):
    if "sacrifice a clue" not in face.oracle_text.casefold():
        return face
    rows = [(node.text, material_line_for(node.text), node.span) for node in face.nodes]
    pair = linked_clue_copy_source(rows, source_name=face.face_name)
    if pair is None:
        return face
    by_span = {node.span: node for node in face.nodes}
    attack, sacrifice = by_span[pair.attack], by_span[pair.sacrifice]
    if (attack.kind != "triggered_ability" or sacrifice.kind != "triggered_ability"
            or attack.active_zone != "battlefield" or sacrifice.active_zone != "battlefield"
            or not attack.exact or attack.residual_ids or attack.event != "creature.attacks"
            or attack.event_condition != linked_clue_attack_condition()
            or attack.target_schema != linked_clue_attack_target()
            or attack.effects != linked_clue_attack_effects(linked=False)
            or sacrifice.event != "permanent.sacrificed" or sacrifice.effects or sacrifice.handlers
            or sacrifice.event_condition != linked_clue_sacrifice_condition()
            or sacrifice.target_schema is not None):
        return face
    gate = explicit_capabilities_gate(
        (*attack.capability_dependencies, *LINKED_CLUE_COPY_DEPENDENCIES),
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    if gate.blockers or gate.closure is None:
        return face
    closure = gate.closure
    fields = {
        "capability_dependencies": gate.capabilities, "capability_closure": closure.reachable,
        "capability_profile": closure.profile, "capability_fingerprint": closure.fingerprint,
    }
    updated = {
        attack.node_id: replace(attack, effects=linked_clue_attack_effects(), **fields),
        sacrifice.node_id: replace(
            sacrifice, lowerable=True, exact=True, template_id="linked-clue-copy-v1",
            effects=(linked_clue_choice_effect(),),
            mechanics=(*sacrifice.mechanics, "fixed-typed-event-effect-trigger", "trigger-event-normalized-zone-change"),
            residual_ids=(), **fields,
        ),
    }
    return replace(
        face, nodes=tuple(updated.get(node.node_id, node) for node in face.nodes),
        residuals=tuple(r for r in face.residuals if r.residual_id not in sacrifice.residual_ids),
    )


def compile_linked_clue_copy_faces(faces, *, material_line_for, capability_registry, capability_profile):
    return tuple(_compile_face_pair(
        face, material_line_for=material_line_for, capability_registry=capability_registry,
        capability_profile=capability_profile,
    ) for face in faces)
