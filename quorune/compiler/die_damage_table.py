"""Compile the complete printed three-row creature-damage table."""

from ..rules.die_damage_table import DEPENDENCIES, creature_damage_table_effect
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan


def compile_die_damage_table_face(
    *, face_id, face_name, oracle_text, material_rows, capability_registry, capability_profile,
):
    if len(material_rows) != 4 or capability_registry is None:
        return None
    named = SourceReferenceSpec(face_name).regex_pattern
    rows = (
        r"Roll a d20\.",
        rf"1[—–]9 \| {named} deals 3 damage to each creature\.",
        rf"10[—–]19 \| You may choose a creature\. {named} deals 3 damage to each creature not chosen this way\.",
        rf"20 \| {named} deals 6 damage to each creature your opponents control\.",
    )
    if any(re.fullmatch(pattern, row[1].strip(), re.I) is None for pattern, row in zip(rows, material_rows, strict=True)):
        return None
    gate = explicit_capabilities_gate(
        DEPENDENCIES, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    if gate.blockers or gate.closure is None:
        return None
    span = SourceSpan(material_rows[0][2].start, material_rows[-1][2].end, material_rows[0][2].line)
    node = OracleNode(
        node_id=f"{face_id}:n1", kind="spell_ability", text=oracle_text[span.start:span.end], span=span,
        active_zone="stack", event="resolve", lowerable=True, exact=True,
        template_id="d20-creature-damage-table-v1", effects=(creature_damage_table_effect(),),
        mechanics=("cr-120-damage",), capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable, capability_profile=gate.closure.profile,
        capability_fingerprint=gate.closure.fingerprint,
    )
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text, nodes=(node,), residuals=())
