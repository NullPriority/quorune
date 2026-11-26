from __future__ import annotations

"""Recognize the linked positive-X entry target and its Equipment follow-up."""

from . import regex_working_set as re
from ..rules.kicked_artifact_entry import (
    KICKED_ARTIFACT_ENTRY_CAPABILITY, kicked_artifact_entry_condition,
    kicked_artifact_entry_effects, kicked_artifact_entry_schema,
)
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, append_residual
from .kicked_entry_trigger_nodes import KICKED_ENTRY_TRIGGER_CAPABILITY, KICKED_ENTRY_TRIGGER_MECHANIC
from .kicker_nodes import single_positive_generic_kicker_for_source


_ENTRY = re.compile(
    r"When this creature enters, if it was kicked, gain control of target artifact with mana value X or less\. "
    r"If that artifact is an Equipment, attach it to this creature\.", re.IGNORECASE,
)


def kicked_artifact_entry_node(
    *, record, face_id, node_id, line, material_line, span, printed_card_types,
    trusted_mechanics, capability_registry, capability_profile, residuals,
):
    if (not _ENTRY.fullmatch(material_line.strip()) or "creature" not in printed_card_types
            or single_positive_generic_kicker_for_source(record, face_id) is None):
        return None
    effects, targets = kicked_artifact_entry_effects(), kicked_artifact_entry_schema()
    mechanics = (
        "cr-603-handling-triggered-abilities", KICKED_ENTRY_TRIGGER_MECHANIC,
        "fixed-typed-event-effect-trigger", "fixed-resolution-control", "cr-115-targets",
        "cr-611-continuous-effects",
    )
    gate = dependency_gate(
        mechanics=mechanics, effects=effects, target_schema=targets,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=(KICKED_ARTIFACT_ENTRY_CAPABILITY, KICKED_ENTRY_TRIGGER_CAPABILITY),
    )
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="Kicked artifact entry requires sealed X, persistent control and legal attachment owners",
            blockers=gate.blockers,
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event="permanent.enter.self",
        event_condition=kicked_artifact_entry_condition(), lowerable=True, exact=not residual_ids,
        template_id="kicked-entry-artifact-control-and-equipment-v1", effects=effects, target_schema=targets,
        mechanics=mechanics, residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (), capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
