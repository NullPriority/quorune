"""Pair a source's public graveyard exile with its Food activation grant."""

from dataclasses import replace
from hashlib import sha256

from ..linked_exile_activation_model import LINKED_EXILE_ACTIVATION_EVENT, grant_descriptor
from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
from ..rules.linked_exile_activations import (
    LINKED_ACTIVATION_TRIGGER_DEPENDENCIES, graveyard_exile_and_food_effects, is_graveyard_exile_and_food_trigger,
)
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from ..semantic_runtime.linked_exile_activations import LinkedExileActivatedGrantHandler
from .dependency_gate import explicit_capabilities_gate


_TRIGGER = "exile up to one target card from a graveyard and create a food token."
_STATIC = "foods you control have all activated abilities of all creature cards exiled with "


def _normalized(text):
    return " ".join(text.replace("’", "'").casefold().split())


def _source_matches(sources, text):
    return source_self_permanent_type(text) in {"creature", "permanent"} or sources.matches(text)


def _compile_pair(face, *, material_line_for, capability_registry, capability_profile):
    sources = SourceReferenceSpec(_normalized(face.face_name))
    triggers, grants = [], []
    for node in face.nodes:
        text = _normalized(material_line_for(node.text))
        lead, separator, body = text.rpartition(", ")
        if (node.kind == "triggered_ability" and node.exact and separator and body == _TRIGGER
                and lead.startswith("whenever ") and lead.endswith(" enters or attacks")
                and _source_matches(sources, lead.removeprefix("whenever ").removesuffix(" enters or attacks"))
                and is_graveyard_exile_and_food_trigger(node, event_condition=node.event_condition)
                and node.effects == graveyard_exile_and_food_effects()):
            triggers.append(node)
        if (node.kind == "static_ability" and not node.exact and not node.effects and not node.handlers
                and text.startswith(_STATIC) and text.endswith(".")
                and _source_matches(sources, text.removeprefix(_STATIC).removesuffix("."))):
            grants.append(node)
    if len(triggers) != 1 or len(grants) != 1:
        return face
    trigger, grant = triggers[0], grants[0]
    identity = f"{face.face_name}:{face.face_id}:{trigger.span.line}:{grant.span.line}"
    binding = LINKED_ABILITY_BINDING_PREFIX + sha256(identity.encode("utf-8")).hexdigest()[:24]
    gates = tuple(explicit_capabilities_gate(
        dependencies, capability_registry=capability_registry, capability_profile=capability_profile,
    ) for dependencies in (
        (*trigger.capability_dependencies, *LINKED_ACTIVATION_TRIGGER_DEPENDENCIES),
        LinkedExileActivatedGrantHandler().capability_dependencies,
    ))
    if any(gate.blockers or gate.closure is None for gate in gates):
        return face
    updated = (
        replace(trigger, effects=graveyard_exile_and_food_effects(binding)),
        replace(grant, event=LINKED_EXILE_ACTIVATION_EVENT, lowerable=True, exact=True,
                template_id="linked-exile-food-activated-grant-v1", handlers=(grant_descriptor(binding),),
                residual_ids=()),
    )
    by_id = {
        node.node_id: replace(node, capability_dependencies=gate.capabilities,
                              capability_closure=gate.closure.reachable, capability_profile=gate.closure.profile,
                              capability_fingerprint=gate.closure.fingerprint)
        for node, gate in zip(updated, gates, strict=True)
    }
    return replace(face, nodes=tuple(by_id.get(node.node_id, node) for node in face.nodes),
                   residuals=tuple(row for row in face.residuals if row.residual_id not in grant.residual_ids))


def compile_linked_exile_activation_faces(faces, *, material_line_for, capability_registry, capability_profile):
    return tuple(
        _compile_pair(face, material_line_for=material_line_for, capability_registry=capability_registry,
                      capability_profile=capability_profile)
        if _STATIC in _normalized(face.oracle_text) else face
        for face in faces
    )
