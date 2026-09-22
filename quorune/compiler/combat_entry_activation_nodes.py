from __future__ import annotations

from typing import Any

from ..cast_lifecycles import FIXED_CAST_LIFECYCLE_CAPABILITY_ID
from ..combat_entry_activations import (
    compile_fixed_encore,
    compile_fixed_ninjutsu,
    ENCORE_EFFECT_OPERATION,
    ENCORE_MECHANIC_ID,
    fixed_encore_handler_descriptor,
    fixed_ninjutsu_handler_descriptor,
    FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
    NINJUTSU_EFFECT_OPERATION,
    NINJUTSU_MECHANICS,
)
from ..rules.capabilities import CapabilityRegistry
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capability_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan


def fixed_ninjutsu_keyword_node(
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
    if len(mechanics) != 1 or mechanics[0] not in NINJUTSU_MECHANICS:
        return None
    spec = compile_fixed_ninjutsu(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_ninjutsu_cost",
            text=line,
            span=span,
            reason="Ninjutsu cost is outside the fixed ordinary-mana grammar",
            blockers=(
                "variable, hybrid, Phyrexian, snow, and nonmana costs",
                "modified, copied, granted, combined, or repeated instances",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="activated_ability",
            text=line,
            span=span,
            active_zone="hand",
            event="activate",
            lowerable=False,
            exact=False,
            template_id="fixed-ninjutsu-activation-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    gate = explicit_capability_gate(
        FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
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
                reason="Fixed Ninjutsu lacks trusted lifecycle closure",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="activated_ability",
        text=line,
        span=span,
        active_zone="hand",
        event="activate",
        lowerable=True,
        exact=not residual_ids,
        template_id=(
            "fixed-commander-ninjutsu-activation-v1"
            if spec.commander
            else "fixed-ninjutsu-activation-v1"
        ),
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=(
            {
                "op": NINJUTSU_EFFECT_OPERATION,
                "attack_target": f"$context.fixed_combat_return",
            },
        ),
        handlers=(fixed_ninjutsu_handler_descriptor(spec),),
        runtime_coverage=(
            "hand_or_command_activation",
            "typed_unblocked_attacker_return_cost",
            "public_reveal_while_on_stack",
            "same_recipient_tapped_attacking_entry",
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


__all__ = ["fixed_ninjutsu_keyword_node"]


def fixed_encore_keyword_node(
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
    if mechanics != (ENCORE_MECHANIC_ID,):
        return None
    spec = compile_fixed_encore(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_encore_cost",
            text=line,
            span=span,
            reason="Encore cost is outside the fixed ordinary-mana grammar",
            blockers=(
                "variable, hybrid, Phyrexian, snow, and nonmana costs",
                "modified, copied, granted, combined, or repeated instances",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="activated_ability",
            text=line,
            span=span,
            active_zone="graveyard",
            event="activate",
            lowerable=False,
            exact=False,
            template_id="fixed-encore-activation-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    gate = explicit_capability_gate(
        FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
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
                reason="Fixed Encore lacks trusted lifecycle closure",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="activated_ability",
        text=line,
        span=span,
        active_zone="graveyard",
        event="activate",
        lowerable=True,
        exact=not residual_ids,
        template_id="fixed-encore-activation-v1",
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=({"op": ENCORE_EFFECT_OPERATION},),
        handlers=(fixed_encore_handler_descriptor(spec),),
        runtime_coverage=(
            "graveyard_sorcery_activation",
            "source_exile_cost",
            "replacement_aware_copy_tokens_per_opponent",
            "zone_object_haste",
            "opponent_specific_attack_if_able",
            "identity_pinned_delayed_sacrifice",
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


__all__ = ["fixed_encore_keyword_node", "fixed_ninjutsu_keyword_node"]
