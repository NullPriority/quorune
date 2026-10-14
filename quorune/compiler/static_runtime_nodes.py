from __future__ import annotations

"""CardProgram nodes for closed static runtime-handler productions."""

from typing import AbstractSet, Any, Mapping

from ..rules.capabilities import CapabilityRegistry
from ..characteristic_fragments import characteristic_quantity_capabilities
from .dependency_gate import explicit_capabilities_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan
from .runtime_templates import static_runtime_template


_DAMAGEABLE_CARD_TYPES = frozenset({"battle", "creature", "planeswalker"})


def runtime_handler_node(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    compiled: tuple[
        str,
        Mapping[str, Any] | tuple[Mapping[str, Any], ...],
        str | tuple[str, ...],
    ],
    kind: str,
    event: str,
    active_zone: str = "battlefield",
    runtime_coverage: tuple[str, ...] = (),
    dependency_reason: str,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    """Lower one already-compiled runtime descriptor into a guarded node."""

    template_id, raw_handlers, capabilities = compiled
    handlers = (
        raw_handlers
        if isinstance(raw_handlers, tuple)
        else (raw_handlers,)
    )
    gate = explicit_capabilities_gate(
        (*((capabilities,) if isinstance(capabilities, str) else capabilities),
            *characteristic_quantity_capabilities(handlers)),
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
                reason=dependency_reason,
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind=kind,
        text=line,
        span=span,
        active_zone=active_zone,
        event=event,
        lowerable=True,
        exact=not gate.blockers,
        template_id=template_id,
        handlers=handlers,
        runtime_coverage=runtime_coverage,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def static_runtime_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    source_name: str,
    card_types: AbstractSet[str],
    permanent_card_types: AbstractSet[str],
    source_is_class: bool,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one closed static descriptor and its capability evidence."""

    from .devotion_templates import fixed_devotion_increase_handler
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    devotion = (fixed_devotion_increase_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if devotion is not None:
        return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=devotion,
            kind='static_ability',event='devotion.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Devotion requires its current public-query capability',
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    template = static_runtime_template(
        material_line,
        source_name=source_name,
        source_damageable=bool(card_types.intersection(_DAMAGEABLE_CARD_TYPES)),
        source_permanent=bool(card_types.intersection(permanent_card_types)),
        source_is_class=source_is_class,
    )
    if template is None:
        return None
    return runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=template.compiled,
        kind=template.kind,
        event=template.event,
        active_zone=template.active_zone,
        runtime_coverage=template.runtime_coverage,
        dependency_reason=template.dependency_reason,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


__all__ = ["runtime_handler_node", "static_runtime_node"]
