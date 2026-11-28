from __future__ import annotations

"""Compile an entire fixed die result table as one resolving instruction."""

from ..stack_target_change_model import power_adjusted_retarget_roll, power_adjusted_retarget_target_schema
from . import regex_working_set as re
from .dependency_gate import dependency_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual


_POWER_RETARGET_TABLE = (
    re.compile(
        r"Choose target spell or ability with one or more targets\. Roll a d20 and add "
        r"the greatest power among creatures you control\.", re.I,
    ),
    re.compile(r"1[—–-]14\s*\|\s*You may choose new targets for that spell or ability\.", re.I),
    re.compile(
        r"15\+\s*\|\s*You may choose new targets for that spell or ability\. Then copy it\. "
        r"You may choose new targets for the copy\.", re.I,
    ),
)


def compile_die_table_spell_face(*, face_id, face_name, oracle_text, material_rows,
                                 trusted_mechanics, capability_registry, capability_profile, residuals):
    if (len(material_rows) != len(_POWER_RETARGET_TABLE)
            or not all(pattern.fullmatch(" ".join(row[1].split()))
                       for pattern, row in zip(_POWER_RETARGET_TABLE, material_rows))):
        return None
    effects = (power_adjusted_retarget_roll(),)
    schema = power_adjusted_retarget_target_schema()
    mechanics = ("cr-115-targets",)
    gate = dependency_gate(
        mechanics=mechanics, effects=effects, target_schema=schema,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    span = SourceSpan(material_rows[0][2].start, material_rows[-1][2].end, material_rows[0][2].line)
    residual_ids = (
        append_residual(residuals, kind="dependency_contract", text=oracle_text, span=span,
                        reason="Die table requires the complete stack-target result", blockers=gate.blockers),
    ) if gate.blockers else ()
    node = OracleNode(
        node_id=f"{face_id}:n1", kind="spell_ability", text=oracle_text, span=span, active_zone="stack",
        event="resolve", lowerable=True, exact=not residual_ids, template_id="power-adjusted-stack-targets-d20-v1",
        effects=effects, target_schema=schema, mechanics=mechanics, residual_ids=residual_ids,
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text, nodes=(node,), residuals=tuple(residuals))
