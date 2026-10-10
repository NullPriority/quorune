from __future__ import annotations

"""Compile the triggered half of fixed Graft through shared typed owners."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec,
)
from ..semantic_choices.graft import GRAFT_CAPABILITY, GRAFT_COVERAGE
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def graft_lifecycle_node(*, node_id, line, span, capability_registry, capability_profile, residuals):
    gate = explicit_capabilities_gate((GRAFT_CAPABILITY, "counter.placement.quantity_replacement",
        "trigger.condition.fixed_public_state"), capability_registry=capability_registry,
        capability_profile=capability_profile)
    ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Graft requires optional atomic current-counter movement", blockers=gate.blockers),) if gate.blockers else ()
    condition = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST,
                                             amount=1, counter_name="+1/+1")
    return OracleNode(node_id=f"{node_id}:lifecycle", kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event="permanent.enter", lowerable=True, exact=not ids,
        template_id="graft-fixed-counter-movement-v1",
        event_condition={"all": [
            {"field": "types", "op": "contains_any", "value": ["creature"]},
            {"field": "card", "op": "ne", "value": "$source.ref"},
            {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "eq", "value": True,
             "condition": condition.to_dict()}]},
        effects=({"op": "offer_graft_counter_movement", "player": "$controller",
            "from_ref": "$source", "from_identity": "$context.source_logical_object_id",
            "to_ref": "$context.card", "to_identity": "$context.graft_recipient_identity"},),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE, GRAFT_COVERAGE, "intervening_condition"),
        mechanics=("graft", "cr-122-counters", "cr-603-handling-triggered-abilities"),
        residual_ids=ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
