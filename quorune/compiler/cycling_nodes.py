from __future__ import annotations

from typing import Any
from dataclasses import replace

from ..cycling_abilities import (
    CYCLING_MECHANIC_ID,
    VARIABLE_CYCLING_CAPABILITY,
    compile_ordinary_cycling_ability,
    compile_typecycling_ability,
    ordinary_cycling_handler_descriptor,
    typecycling_handler_descriptor,
)
from ..rules.capabilities import CapabilityRegistry
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capability_gate, explicit_capabilities_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan


TYPECYCLING_CAPABILITY_ID = "activation.typecycling.hand"


def typecycling_keyword_node(
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
    """Lower one fixed Typecycling keyword through the shared search owner."""

    if mechanics != (CYCLING_MECHANIC_ID,):
        return None
    spec = compile_typecycling_ability(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        return None
    if ":typecycling-instance:" in node_id:
        instance = node_id.rsplit(":typecycling-instance:", 1)[1]
        if instance not in {"1", "2", "3", "4"}:
            return None
        spec = replace(spec, ability_id=spec.ability_id + ":typecycling-instance:" + instance)
    gate = explicit_capability_gate(
        TYPECYCLING_CAPABILITY_ID,
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
                reason=(
                    "Typecycling activation lacks a trusted capability closure"
                ),
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
        template_id="fixed-typecycling-activation-v1",
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=(spec.search_effect(),),
        handlers=(typecycling_handler_descriptor(spec),),
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


def ordinary_cycling_keyword_node(
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
    if mechanics != (CYCLING_MECHANIC_ID,):
        return None
    spec = compile_ordinary_cycling_ability(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_cycling_cost",
            text=line,
            span=span,
            reason=(
                "Cycling cost is outside the closed fixed ordinary mana grammar"
            ),
            blockers=(
                "variable and hybrid Cycling costs",
                "nonmana Cycling costs",
                "unsupported Typecycling, modifiers, prohibitions, and "
                "Cycling triggers",
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
            template_id="ordinary-cycling-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    from ..activation_x import ANNOUNCED_ACTIVATION_X_CAPABILITY

    variable = spec.announced_x_symbols is not None
    direct = ("activation.cycling.hand", "zone.draw.library_to_hand")
    if variable:
        direct = (*direct, VARIABLE_CYCLING_CAPABILITY, ANNOUNCED_ACTIVATION_X_CAPABILITY)
    gate = explicit_capabilities_gate(
        direct,
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
                reason=(
                    "ordinary Cycling activation lacks a trusted capability closure"
                ),
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
        template_id="ordinary-cycling-activation-v2" if variable else "ordinary-cycling-activation-v1",
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=(
            {
                "op": "draw",
                "player": "$controller",
                "count": 1,
                "private": True,
            },
        ),
        handlers=(ordinary_cycling_handler_descriptor(spec),),
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
    "TYPECYCLING_CAPABILITY_ID",
    "ordinary_cycling_keyword_node",
    "cycling_x_token_node",
    "typecycling_keyword_node",
]



def cycling_x_token_node(
    *, record, node_id, line, material_line, span, source_name, capability_registry, capability_profile,
):
    from . import regex_working_set as re
    from .fixed_counter_trigger_nodes import fixed_counter_trigger_binding
    from ..rules.cycling_activation_x import TRIGGER_CAPABILITIES, cycling_x_token_effect

    if not re.fullmatch(
        r"When you cycle this card, create an X/X blue Shark creature token with flying\.",
        material_line.strip(), re.I,
    ):
        return None
    if capability_registry is None or record.faces:
        return None
    prices = [
        compile_ordinary_cycling_ability(
            material_line=re.sub(r"\s+\([^()]*\)\s*$", "", raw), oracle_line=raw, line_index=index,
        )
        for index, raw in enumerate(record.oracle_text.splitlines())
    ]
    if sum(price is not None and price.announced_x_symbols == 1 for price in prices) != 1:
        return None
    binding = fixed_counter_trigger_binding(material_line, card_name=source_name)
    if binding is None or (binding.event.value, binding.variant) != ("card.cycled.self", "source_cycles"):
        return None
    gate = explicit_capabilities_gate(
        TRIGGER_CAPABILITIES, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    if gate.blockers or gate.closure is None:
        return None
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="hand",
        event=binding.event.value, event_condition=binding.event_condition,
        lowerable=True, exact=True, template_id="self-cycled-announced-x-shark-token-v1",
        effects=(cycling_x_token_effect(),), mechanics=("cr-603-handling-triggered-abilities", *binding.event_mechanics),
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable,
        capability_profile=gate.closure.profile, capability_fingerprint=gate.closure.fingerprint,
    )
