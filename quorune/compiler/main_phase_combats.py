from __future__ import annotations

from . import regex_working_set as re
from .dependency_gate import dependency_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual
from ..rules.main_phase_combat_model import (
    IMMEDIATE_COMBAT_MECHANIC, MAIN_COMBAT_MECHANIC, MainPhaseCombatInstruction, main_phase_combat_effect,
)


_PRINTED = re.compile(
    r"After this main phase, there are two additional combat phases\. "
    r"At the beginning of each combat this turn, untap all creatures that attacked this turn\.",
    re.IGNORECASE,
)
_IMMEDIATE = re.compile(
    r"Untap all creatures that attacked this turn\. After this main phase, there is an additional "
    r"combat phase followed by an additional main phase\.",
    re.IGNORECASE,
)
_TEMPLATE = f"{MAIN_COMBAT_MECHANIC}-v1"


def compile_main_phase_combat_face(
    *, face_id, face_name, oracle_text, material_rows, trusted_mechanics,
    capability_registry, capability_profile, residuals,
):
    printed = " ".join(row[1] for row in material_rows)
    if _PRINTED.fullmatch(printed):
        instruction = MainPhaseCombatInstruction.TWO_COMBATS_DELAYED_UNTAP
        template_id = _TEMPLATE
    elif _IMMEDIATE.fullmatch(printed):
        instruction = MainPhaseCombatInstruction.COMBAT_THEN_MAIN_IMMEDIATE_UNTAP
        template_id = f"{IMMEDIATE_COMBAT_MECHANIC}-v1"
    else:
        return None
    effects = (main_phase_combat_effect(instruction),)
    gate = dependency_gate(
        mechanics=(), effects=effects, target_schema=None, trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    start, end = material_rows[0][2], material_rows[-1][2]
    span = SourceSpan(start.start, end.end, start.line)
    blockers = gate.blockers or (() if gate.closure else ("complete main-phase combat schedule authority",))
    residual_ids = (append_residual(
        residuals, kind="dependency_contract", text=oracle_text, span=span,
        reason="The additional phases and attacker untap need their closed joint contract", blockers=blockers,
    ),) if blockers else ()
    node = OracleNode(
        node_id=f"{face_id}:n1", kind="spell_ability", text=oracle_text, span=span, active_zone="stack", event="resolve",
        lowerable=True, exact=not residual_ids, template_id=template_id, effects=effects,
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text, nodes=(node,), residuals=tuple(residuals))
