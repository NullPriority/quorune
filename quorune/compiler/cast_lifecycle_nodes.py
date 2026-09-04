from __future__ import annotations

"""Source-spanned nodes for fixed public casting lifecycles."""

from typing import Any

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..cast_lifecycles import (
    compile_fixed_cast_lifecycle,
    FixedCastLifecycleKind,
    fixed_cast_lifecycle_handler_descriptor,
    FIXED_CAST_LIFECYCLE_CAPABILITY_ID,
    FIXED_CAST_LIFECYCLE_RUNTIME_EVENT,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


FIXED_CAST_LIFECYCLE_TEMPLATE_ID = "fixed-public-cast-lifecycle-v1"
_MECHANICS = frozenset(kind.value for kind in FixedCastLifecycleKind)
_RETRACE_DISCARD_CAPABILITY_ID = (
    "casting.additional_cost.zone_change.fixed_discard"
)


def fixed_cast_lifecycle_keyword_node(
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
) -> OracleNode | None:
    """Lower one bounded lifecycle keyword or reject its cost grammar."""

    if len(mechanics) != 1 or mechanics[0] not in _MECHANICS:
        return None
    spec = compile_fixed_cast_lifecycle(
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
            reason="Casting lifecycle is outside the fixed public grammar",
            blockers=(
                "ordinary fixed-mana Buyback, Dash, or Warp, or bare Retrace",
                "variable, hybrid, Phyrexian, snow, nonmana, modified, copied, or granted costs",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="keyword_ability",
            text=line,
            span=span,
            active_zone="all",
            event=FIXED_CAST_LIFECYCLE_RUNTIME_EVENT,
            lowerable=False,
            exact=False,
            template_id="fixed-public-cast-lifecycle-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    dependencies = (
        FIXED_CAST_LIFECYCLE_CAPABILITY_ID,
        *(
            (_RETRACE_DISCARD_CAPABILITY_ID,)
            if spec.kind is FixedCastLifecycleKind.RETRACE
            else ()
        ),
    )
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
                reason="Fixed casting lifecycle lacks trusted capability closure",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    coverage = (
        *{
            FixedCastLifecycleKind.BUYBACK: (
                "fixed_mana_optional_additional_cost",
                "replacement_aware_resolution_destination",
            ),
            FixedCastLifecycleKind.DASH: (
                "fixed_mana_alternate_cost",
                "zone_object_haste",
                "identity_pinned_delayed_return",
            ),
            FixedCastLifecycleKind.WARP: (
                "fixed_mana_alternate_cost",
                "identity_pinned_delayed_exile",
                "later_turn_exile_cast_permission",
            ),
            FixedCastLifecycleKind.RETRACE: (
                "owner_graveyard_cast_permission",
                "typed_land_discard_additional_cost",
            ),
        }[spec.kind],
        CURRENT_ABILITY_FRAGMENT_COVERAGE,
    )
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="all",
        event=FIXED_CAST_LIFECYCLE_RUNTIME_EVENT,
        lowerable=True,
        exact=not residual_ids,
        template_id=FIXED_CAST_LIFECYCLE_TEMPLATE_ID,
        handlers=(fixed_cast_lifecycle_handler_descriptor(spec),),
        runtime_coverage=coverage,
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


__all__ = [
    "FIXED_CAST_LIFECYCLE_TEMPLATE_ID",
    "fixed_cast_lifecycle_keyword_node",
]
