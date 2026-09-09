from __future__ import annotations

"""Closed Living Weapon and For Mirrodin! source-entry triggers."""

from typing import Mapping

from ..rules.attachment_actions import (
    FOR_MIRRODIN_CAPABILITY,
    LIVING_WEAPON_CAPABILITY,
)
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual
from .token_templates import fixed_token_creation_effect_template
from ..rules.capabilities import CapabilityRegistry


_SPECS = {
    "living weapon": (
        LIVING_WEAPON_CAPABILITY,
        "Create a 0/0 black Phyrexian Germ creature token.",
    ),
    "for mirrodin!": (
        FOR_MIRRODIN_CAPABILITY,
        "Create a 2/2 red Rebel creature token.",
    ),
}


def fixed_attachment_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one exact token-then-source-attachment keyword trigger."""

    normalized = material_line.strip().rstrip(".").casefold()
    spec = _SPECS.get(normalized)
    if spec is None or mechanics != (normalized,):
        return None
    capability_id, token_text = spec
    token = fixed_token_creation_effect_template(token_text)
    if token is None:
        raise ValueError("Fixed attachment keyword token profile is unavailable")
    token_effect = dict(token.effect)
    effect: Mapping[str, object] = {
        **token_effect,
        "op": "create_attached_token",
        "source": "$source.zone_object",
    }
    gate = explicit_capability_gate(
        capability_id,
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
                reason="attachment keyword depends on blocked typed capabilities",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="permanent.enter.self",
        lowerable=True,
        exact=not residual_ids,
        template_id="create-attached-equipment-token-v1",
        effects=(effect,),
        mechanics=(normalized, "cr-701-3-attach"),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


__all__ = ["fixed_attachment_keyword_node"]
