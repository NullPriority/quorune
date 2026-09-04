from __future__ import annotations

"""Closed keyword lowering for token-producing triggered abilities."""

import re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..fixed_token_production import (
    AFTERLIFE_CAPABILITY_ID,
    AFTERLIFE_MECHANIC_ID,
    FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
    FixedAfterlifeSpec,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def fixed_afterlife_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    **_unused: object,
) -> OracleNode | None:
    """Lower one canonical positive fixed Afterlife instance."""

    if mechanics != (AFTERLIFE_MECHANIC_ID,):
        return None
    match = re.fullmatch(
        r"Afterlife (?P<count>[1-9]\d*)\.?",
        material_line.strip(),
        re.IGNORECASE,
    )
    spec = None
    if match is not None:
        try:
            spec = FixedAfterlifeSpec(int(match.group("count")))
        except ValueError:
            spec = None
    gate = explicit_capability_gate(
        AFTERLIFE_CAPABILITY_ID,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = (
        gate.blockers
        if spec is not None
        else ("fixed positive Afterlife count from one to twenty",)
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind=(
                    "dependency_contract"
                    if spec is not None
                    else "keyword_grammar"
                ),
                text=line,
                span=span,
                reason=(
                    "Afterlife depends on its typed death-trigger token owner"
                    if spec is not None
                    else "Afterlife count is outside the fixed positive grammar"
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
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="permanent.graveyard.self",
        lowerable=spec is not None,
        exact=spec is not None and not residual_ids,
        template_id=(
            "afterlife-fixed-token-trigger-v1" if spec is not None else None
        ),
        effects=((spec.effect(),) if spec is not None else ()),
        runtime_coverage=(
            (CURRENT_ABILITY_FRAGMENT_COVERAGE,)
            if spec is not None
            else ()
        ),
        mechanics=(
            AFTERLIFE_MECHANIC_ID,
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
            "cr-111-tokens",
            "cr-603-handling-triggered-abilities",
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


__all__ = ["fixed_afterlife_keyword_node"]
