from __future__ import annotations

import re
from typing import Any

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..combat_entry_activations import (
    FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
)
from ..rules.capabilities import CapabilityRegistry
from ..semantic_choices.attacking_tokens import (
    MYRIAD_TOKEN_DESTINATION_OPERATION,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


MYRIAD_MECHANIC_ID = "myriad"


def fixed_myriad_keyword_node(
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
    if mechanics != (MYRIAD_MECHANIC_ID,):
        return None
    if re.fullmatch(r"Myriad\.?", material_line.strip(), re.IGNORECASE) is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_myriad_grammar",
            text=line,
            span=span,
            reason="Myriad declaration is outside the closed bare grammar",
            blockers=("one isolated printed Myriad instance",),
        )
        return OracleNode(
            node_id=node_id,
            kind="triggered_ability",
            text=line,
            span=span,
            active_zone="battlefield",
            event="creature.attacks.self",
            lowerable=False,
            exact=False,
            template_id="fixed-myriad-trigger-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    gate = explicit_capabilities_gate(
        (FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,),
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
                reason="Fixed Myriad lacks trusted trigger/token closure",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="creature.attacks.self",
        event_condition={"field": "card", "op": "eq", "value": "$source.ref"},
        lowerable=True,
        exact=not residual_ids,
        template_id="fixed-myriad-trigger-v1",
        effects=(
            {
                "op": MYRIAD_TOKEN_DESTINATION_OPERATION,
                "player": "$controller",
                "copy_of": "$source",
                "copy_snapshot": "$context.myriad_copy_snapshot",
                "defending_player": "$context.defending_player",
            },
        ),
        runtime_coverage=(
            "normalized_self_attack_trigger",
            "optional_copy_per_other_opponent",
            "public_attacking_destination_choice",
            "replacement_aware_copy_token_creation",
            "identity_pinned_end_combat_exile",
            CURRENT_ABILITY_FRAGMENT_COVERAGE,
        ),
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


__all__ = ["fixed_myriad_keyword_node", "MYRIAD_MECHANIC_ID"]
