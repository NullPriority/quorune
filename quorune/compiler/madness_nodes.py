from __future__ import annotations

"""Source-spanned nodes for ordinary fixed-mana Madness."""

from typing import Any

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..madness import (
    fixed_madness_spec,
    MADNESS_CAPABILITY_ID,
    MADNESS_CHOICE_OPERATION,
    MADNESS_DISCARD_CAPABILITY_ID,
    MADNESS_REPLACEMENT_EVENT,
    MADNESS_REPLACEMENT_TEMPLATE_ID,
    MADNESS_TRIGGER_EVENT,
    MADNESS_TRIGGER_TEMPLATE_ID,
    madness_replacement_handler_descriptor,
    madness_trigger_handler_descriptor,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def _madness_dependency_gate(
    dependencies: tuple[str, ...],
    *,
    line: str,
    span: SourceSpan,
    reason: str,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> tuple[Any, tuple[str, ...]]:
    gate = explicit_capabilities_gate(
        dependencies,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=reason,
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return gate, residual_ids


def madness_keyword_nodes(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    **_unused: Any,
) -> tuple[OracleNode, ...] | None:
    """Lower one Madness line into its replacement and cast trigger."""

    if mechanics != ("madness",):
        return None
    spec = fixed_madness_spec(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="keyword_grammar",
            text=line,
            span=span,
            reason="Madness is outside the ordinary fixed-mana grammar",
            blockers=(
                "fixed ordinary mana without X, hybrid, Phyrexian, snow, life, or nonmana payment",
                "ordinary reminder text or a reminderless declaration",
            ),
        )
        residual = OracleNode(
            node_id=node_id,
            kind="keyword_ability",
            text=line,
            span=span,
            active_zone="all",
            event=MADNESS_REPLACEMENT_EVENT,
            lowerable=False,
            exact=False,
            template_id="madness-fixed-mana-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
        return (residual,)

    replacement_dependencies = (
        MADNESS_CAPABILITY_ID,
        MADNESS_DISCARD_CAPABILITY_ID,
        "zone.change.destination_replacement",
    )
    trigger_dependencies = (
        MADNESS_CAPABILITY_ID,
        MADNESS_DISCARD_CAPABILITY_ID,
        "trigger.event.normalized_zone_change",
        "trigger.placement.apnap",
        "zone.change.destination_replacement",
    )

    gate_context = {
        "line": line,
        "span": span,
        "capability_registry": capability_registry,
        "capability_profile": capability_profile,
        "residuals": residuals,
    }
    replacement_gate, replacement_residuals = _madness_dependency_gate(
        replacement_dependencies,
        reason="Madness discard replacement lacks trusted capability closure",
        **gate_context,
    )
    trigger_gate, trigger_residuals = _madness_dependency_gate(
        trigger_dependencies,
        reason="Madness cast trigger lacks trusted capability closure",
        **gate_context,
    )
    replacement_closure = replacement_gate.closure
    trigger_closure = trigger_gate.closure
    replacement = OracleNode(
        node_id=f"{node_id}:replacement",
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="all",
        event=MADNESS_REPLACEMENT_EVENT,
        lowerable=True,
        exact=not replacement_residuals,
        template_id=MADNESS_REPLACEMENT_TEMPLATE_ID,
        handlers=(madness_replacement_handler_descriptor(spec),),
        runtime_coverage=(
            "typed_discard_cause",
            "replacement_aware_madness_exile",
            CURRENT_ABILITY_FRAGMENT_COVERAGE,
        ),
        mechanics=("madness", "typed-discard-cause"),
        residual_ids=replacement_residuals,
        capability_dependencies=replacement_gate.capabilities,
        capability_closure=(
            replacement_closure.reachable
            if replacement_closure is not None
            else ()
        ),
        capability_profile=(
            replacement_closure.profile
            if replacement_closure is not None
            else None
        ),
        capability_fingerprint=(
            replacement_closure.fingerprint
            if replacement_closure is not None
            else None
        ),
    )
    trigger = OracleNode(
        node_id=f"{node_id}:trigger",
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="exile",
        event=MADNESS_TRIGGER_EVENT,
        lowerable=True,
        exact=not trigger_residuals,
        template_id=MADNESS_TRIGGER_TEMPLATE_ID,
        effects=({"op": MADNESS_CHOICE_OPERATION, "madness": spec.to_dict()},),
        handlers=(madness_trigger_handler_descriptor(spec),),
        runtime_coverage=(
            "typed_discard_cause",
            "replacement_aware_madness_exile",
            "identity_pinned_exile_cast_choice",
        ),
        mechanics=(
            "madness",
            "typed-discard-cause",
            "trigger-event-normalized-zone-change",
            "cr-603-handling-triggered-abilities",
        ),
        residual_ids=trigger_residuals,
        capability_dependencies=trigger_gate.capabilities,
        capability_closure=(
            trigger_closure.reachable if trigger_closure is not None else ()
        ),
        capability_profile=(
            trigger_closure.profile if trigger_closure is not None else None
        ),
        capability_fingerprint=(
            trigger_closure.fingerprint
            if trigger_closure is not None
            else None
        ),
    )
    return replacement, trigger


__all__ = ["madness_keyword_nodes"]
