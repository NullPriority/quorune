"""Pair the complete optional exile with its separate owner-token trigger."""

from dataclasses import replace
from hashlib import sha256

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..linked_exile_return_model import (
    LINKED_ABILITY_BINDING_PREFIX, LINKED_ABILITY_TIMING, LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION,
    LinkedExileReturnSpec,
)
from ..rules.linked_exile_owner_tokens import REQUIREMENTS, TEMPLATE, source_linked_token_effect
from ..rules.linked_exile_return_shapes import linked_ability_exile_node_capabilities
from .dependency_gate import explicit_capabilities_gate


_ENTRY = ("when this creature enters, exile up to one target nonland, nontoken permanent you don't control "
          "with mana value 4 or less.")
_DEPARTURE = ("when this creature leaves the battlefield, the exiled card's owner creates an x/x blue illusion "
              "creature token, where x is the mana value of the exiled card.")
_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "types_none": ["land"], "mana_value_max": 4,
    "controller_relation": "opponent", "token": False, "up_to": 1,
}


def _normalized(value):
    return " ".join(value.replace("’", "'").casefold().split())


def _with_gate(node, gate, **changes):
    return replace(
        node, lowerable=True, exact=True, residual_ids=(), capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable, capability_profile=gate.closure.profile,
        capability_fingerprint=gate.closure.fingerprint,
        runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE))), **changes,
    )


def paired_linked_exile_owner_tokens(nodes, residuals, *, oracle_id, face_id, registry, profile):
    if len(nodes) != 2:
        return nodes
    entry, leave = nodes
    if (_normalized(entry.text) != _ENTRY or _normalized(leave.text) != _DEPARTURE
            or not entry.exact or entry.event != "permanent.enter.self" or entry.event_condition is not None
            or leave.event != "permanent.leave.self" or leave.event_condition is not None
            or entry.target_schema != _TARGET or leave.target_schema is not None
            or tuple(entry.effects) != ({"op": "exile_permanent_targets", "cards": "$targets", "maximum_targets": 1},)):
        return nodes
    identity = f"{oracle_id}:{face_id}:{entry.span.line}:{leave.span.line}"
    binding = LINKED_ABILITY_BINDING_PREFIX + sha256(identity.encode("utf-8")).hexdigest()[:24]
    exile = {
        "op": LINKED_EXILE_RETURN_OPERATION, "phase": "exile", "binding_id": binding, "cards": "$targets",
        "spec": LinkedExileReturnSpec(schema_version=3, timing=LINKED_ABILITY_TIMING).to_dict(),
    }
    mechanics = tuple(mechanic for mechanic in entry.mechanics if mechanic != "fixed-homogeneous-target-set")
    mechanics = (*mechanics, LINKED_EXILE_RETURN_MECHANIC)
    required = linked_ability_exile_node_capabilities(effects=(exile,), target_schema=entry.target_schema, mechanic_ids=mechanics)
    if not required:
        return nodes
    retained = set(entry.capability_dependencies) - {"resolution.effect.fixed_homogeneous_target_set"}
    entry_gate = explicit_capabilities_gate((*retained, *required), capability_registry=registry, capability_profile=profile)
    leave_gate = explicit_capabilities_gate(REQUIREMENTS, capability_registry=registry, capability_profile=profile)
    if entry_gate.blockers or leave_gate.blockers or entry_gate.closure is None or leave_gate.closure is None:
        return nodes
    residuals[:] = [residual for residual in residuals if residual.residual_id not in leave.residual_ids]
    return [
        _with_gate(entry, entry_gate, template_id="linked-optional-target-exile-v1", effects=(exile,), mechanics=mechanics),
        _with_gate(leave, leave_gate, template_id=TEMPLATE, effects=(source_linked_token_effect(binding),),
                   mechanics=(*leave.mechanics, "cr-111-tokens")),
    ]
