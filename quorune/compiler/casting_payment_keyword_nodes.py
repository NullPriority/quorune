from __future__ import annotations

from typing import Any, Callable

from ..casting_payment_keywords import (
    AFFINITY_MECHANIC_ID,
    DELVE_MECHANIC_ID,
    IMPROVISE_MECHANIC_ID,
    compile_affinity,
)
from ..evoke import (
    EVOKE_CAPABILITY_ID,
    EVOKE_MECHANIC_ID,
    compile_fixed_mana_evoke,
)
from ..rules.capabilities import CapabilityRegistry
from ..semantic_runtime.cast_costs import (
    affinity_handler_descriptor,
    delve_handler_descriptor,
    evoke_handler_descriptor,
    improvise_handler_descriptor,
)
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def _literal_payment_keyword_node(
    *,
    mechanic: str,
    capability_id: str,
    template_id: str,
    runtime_coverage: str,
    descriptor: Callable[[], dict[str, Any]],
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    if mechanics != (mechanic,):
        return None
    ordinary = material_line.strip().rstrip(".").casefold() == mechanic
    gate = explicit_capability_gate(
        capability_id,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = gate.blockers if ordinary else (
        f"mechanic:{mechanic}-unsupported-wording",
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract" if ordinary else "keyword_grammar",
                text=line,
                span=span,
                reason=(
                    f"{mechanic.title()} depends on a blocked typed casting-cost capability"
                    if ordinary
                    else f"{mechanic.title()} wording is outside the ordinary keyword grammar"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="stack",
        event="cast.cost",
        lowerable=ordinary,
        exact=ordinary and not residual_ids,
        template_id=template_id if ordinary else None,
        handlers=(descriptor(),) if ordinary else (),
        runtime_coverage=(runtime_coverage,) if ordinary else (),
        mechanics=(mechanic,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def typed_affinity_keyword_node(
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
    if mechanics != (AFFINITY_MECHANIC_ID,):
        return None
    spec = compile_affinity(material_line)
    ordinary = spec is not None
    gate = explicit_capability_gate(
        "casting.payment.affinity",
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = gate.blockers if ordinary else (
        "mechanic:affinity-unsupported-wording",
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract" if ordinary else "keyword_grammar",
                text=line,
                span=span,
                reason=(
                    "Affinity depends on a blocked typed casting-cost capability"
                    if ordinary
                    else "Affinity quality is outside the closed effective-characteristic grammar"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="stack",
        event="cast.cost",
        lowerable=ordinary,
        exact=ordinary and not residual_ids,
        template_id="typed-affinity-effective-query-v2" if ordinary else None,
        handlers=(affinity_handler_descriptor(spec),) if spec is not None else (),
        runtime_coverage=("typed_affinity_effective_query",) if ordinary else (),
        mechanics=(AFFINITY_MECHANIC_ID,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def ordinary_improvise_keyword_node(**values: Any) -> OracleNode | None:
    return _literal_payment_keyword_node(
        mechanic=IMPROVISE_MECHANIC_ID,
        capability_id="casting.payment.improvise",
        template_id="ordinary-improvise-payment-v1",
        runtime_coverage="typed_improvise_payment",
        descriptor=improvise_handler_descriptor,
        **values,
    )


def ordinary_delve_keyword_node(**values: Any) -> OracleNode | None:
    return _literal_payment_keyword_node(
        mechanic=DELVE_MECHANIC_ID,
        capability_id="casting.payment.delve",
        template_id="ordinary-delve-payment-v1",
        runtime_coverage="typed_delve_payment",
        descriptor=delve_handler_descriptor,
        **values,
    )


def fixed_mana_evoke_keyword_node(
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
    if mechanics != (EVOKE_MECHANIC_ID,):
        return None
    spec = compile_fixed_mana_evoke(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    ordinary = spec is not None
    gate = explicit_capability_gate(
        EVOKE_CAPABILITY_ID,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = gate.blockers if ordinary else (
        "fixed ordinary or colored-hybrid Evoke",
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract" if ordinary else "keyword_grammar",
                text=line,
                span=span,
                reason=(
                    "Evoke depends on a blocked typed casting capability"
                    if ordinary
                    else "Evoke cost is outside the fixed mana grammar"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="stack",
        event="cast.cost",
        lowerable=ordinary,
        exact=ordinary and not residual_ids,
        template_id="fixed-mana-evoke-v1" if ordinary else None,
        handlers=(evoke_handler_descriptor(spec),) if spec is not None else (),
        runtime_coverage=(
            "fixed_mana_evoke_alternate_cost",
            "shared_evoke_sacrifice_trigger",
        )
        if ordinary
        else (),
        mechanics=(EVOKE_MECHANIC_ID,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


__all__ = [
    "fixed_mana_evoke_keyword_node",
    "ordinary_delve_keyword_node",
    "ordinary_improvise_keyword_node",
    "typed_affinity_keyword_node",
]
