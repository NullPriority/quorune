from __future__ import annotations

"""Two separate, current-ability-gated Vanishing lifecycle triggers."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


VANISHING_CAPABILITY = "counter.lifecycle.vanishing"


def vanishing_lifecycle_nodes(
    *, node_id: str, line: str, span: SourceSpan,
    capability_registry: CapabilityRegistry | None, capability_profile: str,
    residuals: list[OracleResidual],
) -> tuple[OracleNode, ...]:
    gate = explicit_capabilities_gate(
        (VANISHING_CAPABILITY, "counter.placement.quantity_replacement"),
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Vanishing requires trusted removal events, intervening condition, APNAP and sacrifice owners",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    counter_condition = FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST, amount=1, counter_name="time",
    )
    variants = (
        ("lifecycle", "step.begin", "vanishing_upkeep", {"all": [
            {"field": "player", "op": "eq", "value": "$source.controller"},
            {"field": "step", "op": "eq", "value": "upkeep"},
            {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy",
             "value": True, "condition": counter_condition.to_dict()},
        ]}, (CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE)),
        ("last-time-counter", "counter.removed", "vanishing_sacrifice", {"all": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"field": "counter", "op": "eq", "value": "time"},
            {"field": "counter_before", "op": "gt", "value": 0},
            {"field": "counter_after", "op": "eq", "value": 0},
        ]}, (CURRENT_ABILITY_FRAGMENT_COVERAGE,)),
    )
    return tuple(OracleNode(
        node_id=f"{node_id}:{suffix}", kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event=event, event_condition=condition,
        lowerable=True, exact=not residual_ids, template_id=f"{action.replace('_', '-')}-v1",
        effects=({"op": "fixed_self_counter_keyword_action", "action": action, "amount": 1, "source": "$source"},),
        runtime_coverage=coverage, mechanics=("vanishing", "cr-122-counters"), residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure is not None else (),
        capability_profile=gate.closure.profile if gate.closure is not None else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure is not None else None,
    ) for suffix, event, action, condition, coverage in variants)
