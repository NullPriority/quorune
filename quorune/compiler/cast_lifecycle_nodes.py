from __future__ import annotations

"""Source-spanned nodes for fixed public casting lifecycles."""

from dataclasses import replace
from typing import Any, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..cast_lifecycles import (
    compile_fixed_cast_lifecycle,
    FixedCastLifecycleKind,
    fixed_cast_lifecycle_handler_descriptor,
    FIXED_CAST_LIFECYCLE_CAPABILITY_ID,
    FIXED_ZONE_CAST_LIFECYCLE_CAPABILITY_ID,
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
_ESCAPE_EXILE_CAPABILITY_ID = (
    "casting.additional_cost.zone_change.fixed_exile"
)


def _residual_lifecycle_node(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    residuals: list[OracleResidual],
    reason: str,
    blockers: tuple[str, ...],
) -> OracleNode:
    residual_id = append_residual(
        residuals,
        kind="keyword_grammar",
        text=line,
        span=span,
        reason=reason,
        blockers=blockers,
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


def _face_type_line(record: Any, face_id: str) -> str:
    result = str(record.type_line)
    if face_id == "front" or not getattr(record, "faces", ()):
        return result
    face = next(
        (
            value
            for value in record.faces
            if str(value.get("name") or "") == face_id
        ),
        None,
    )
    return str(face.get("type_line") or result) if face is not None else result


def _lifecycle_face_blocker(
    record: Any,
    face_id: str,
    spec: Any,
) -> tuple[str, str] | None:
    card_types = set(
        _face_type_line(record, face_id)
        .replace("—", "-")
        .split("-", 1)[0]
        .casefold()
        .split()
    )
    if (
        spec.kind
        in {
            FixedCastLifecycleKind.ESCAPE,
            FixedCastLifecycleKind.FORETELL,
            FixedCastLifecycleKind.PLOT,
            FixedCastLifecycleKind.SUSPEND,
        }
        and "land" in card_types
    ):
        return (
            "Cast lifecycle is not supported on a land face",
            "nonland fixed cast lifecycle",
        )
    if spec.kind in {
        FixedCastLifecycleKind.JUMP_START,
        FixedCastLifecycleKind.REBOUND,
    } and not card_types.intersection({"instant", "sorcery"}):
        return (
            f"{spec.kind.value.title()} requires an instant or sorcery face",
            "instant or sorcery cast lifecycle",
        )
    return None


def _lifecycle_runtime_coverage(spec: Any) -> tuple[str, ...]:
    return {
        FixedCastLifecycleKind.BUYBACK: (
            "fixed_mana_optional_additional_cost",
            "replacement_aware_resolution_destination",
        ),
        FixedCastLifecycleKind.DASH: (
            "fixed_mana_alternate_cost",
            "zone_object_haste",
            "identity_pinned_delayed_return",
        ),
        FixedCastLifecycleKind.ESCAPE: (
            "owner_graveyard_cast_permission",
            "fixed_mana_alternate_cost",
            "typed_fixed_other_card_exile_cost",
            "escaped_stack_designation",
        ),
        FixedCastLifecycleKind.FORETELL: (
            "turn_priority_special_action",
            "owner_private_face_down_exile_designation",
            "later_turn_fixed_mana_exile_cast",
        ),
        FixedCastLifecycleKind.JUMP_START: (
            "owner_graveyard_cast_permission",
            "typed_card_discard_additional_cost",
            "mandatory_stack_leave_exile",
        ),
        FixedCastLifecycleKind.PLOT: (
            "sorcery_timing_special_action",
            "public_exile_designation",
            "later_turn_sorcery_timing_free_cast",
        ),
        FixedCastLifecycleKind.REBOUND: (
            "hand_cast_resolution_exile",
            "next_owner_upkeep_optional_free_cast",
            "identity_pinned_exile_trigger",
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
        FixedCastLifecycleKind.SUSPEND: (
            "hand_timing_special_action",
            "face_up_exile_time_counters",
            "owner_upkeep_counter_removal",
            "last_counter_optional_free_cast",
            "identity_pinned_control_duration_haste",
        ),
    }[spec.kind]


def fixed_cast_lifecycle_keyword_node(
    *,
    record: Any,
    face_id: str,
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
        return _residual_lifecycle_node(
            node_id=node_id,
            line=line,
            span=span,
            mechanics=mechanics,
            residuals=residuals,
            reason="Casting lifecycle is outside the fixed public grammar",
            blockers=(
                "closed fixed Buyback, Dash, Escape, Foretell, Plot, Warp, or Suspend, or bare Jump-start, Rebound, or Retrace",
                "variable, hybrid, Phyrexian, snow, nonmana, modified, copied, or granted costs",
            ),
        )
    if spec.kind is FixedCastLifecycleKind.MADNESS:
        return None
    face_blocker = _lifecycle_face_blocker(record, face_id, spec)
    if face_blocker is not None:
        return _residual_lifecycle_node(
            node_id=node_id,
            line=line,
            span=span,
            mechanics=mechanics,
            residuals=residuals,
            reason=face_blocker[0],
            blockers=(face_blocker[1],),
        )
    zone_cast_kinds = {
        FixedCastLifecycleKind.ESCAPE,
        FixedCastLifecycleKind.FORETELL,
        FixedCastLifecycleKind.JUMP_START,
        FixedCastLifecycleKind.PLOT,
        FixedCastLifecycleKind.REBOUND,
    }
    dependencies = (
        (
            FIXED_ZONE_CAST_LIFECYCLE_CAPABILITY_ID
            if spec.kind in zone_cast_kinds
            else FIXED_CAST_LIFECYCLE_CAPABILITY_ID
        ),
        *(
            (_RETRACE_DISCARD_CAPABILITY_ID,)
            if spec.kind
            in {
                FixedCastLifecycleKind.JUMP_START,
                FixedCastLifecycleKind.RETRACE,
            }
            else ()
        ),
        *(
            (_ESCAPE_EXILE_CAPABILITY_ID,)
            if spec.kind is FixedCastLifecycleKind.ESCAPE
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
    coverage = (*_lifecycle_runtime_coverage(spec), CURRENT_ABILITY_FRAGMENT_COVERAGE)
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


def reject_repeated_suspend_nodes(
    nodes: Sequence[OracleNode],
    residuals: list[OracleResidual],
) -> tuple[OracleNode, ...]:
    """Keep repeated instances of one lifecycle kind outside this owner."""

    by_kind: dict[str, list[int]] = {}
    for index, node in enumerate(nodes):
        if node.template_id != FIXED_CAST_LIFECYCLE_TEMPLATE_ID:
            continue
        for handler in node.handlers:
            lifecycle = handler.get("lifecycle")
            if isinstance(lifecycle, dict):
                kind = str(lifecycle.get("kind") or "")
                if kind:
                    by_kind.setdefault(kind, []).append(index)
    positions = sorted(
        index
        for values in by_kind.values()
        if len(values) > 1
        for index in values
    )
    if not positions:
        return tuple(nodes)
    result = list(nodes)
    for index in positions:
        node = result[index]
        residual_id = append_residual(
            residuals,
            kind="keyword_grammar",
            text=node.text,
            span=node.span,
            reason="Repeated cast-lifecycle instances require linked identity",
            blockers=("single fixed instance of each cast lifecycle",),
        )
        result[index] = replace(
            node,
            lowerable=False,
            exact=False,
            template_id="fixed-public-cast-lifecycle-residual-v1",
            handlers=(),
            runtime_coverage=(),
            residual_ids=(residual_id,),
            capability_dependencies=(),
            capability_closure=(),
            capability_profile=None,
            capability_fingerprint=None,
        )
    return tuple(result)


__all__ = [
    "FIXED_CAST_LIFECYCLE_TEMPLATE_ID",
    "fixed_cast_lifecycle_keyword_node",
    "reject_repeated_suspend_nodes",
]
