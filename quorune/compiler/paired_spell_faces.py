from __future__ import annotations

"""Assemble two independently closed spell bodies under one paid text choice."""

from dataclasses import asdict
from ..spell_text_variants import SpellTextVariantKind
from .dependency_gate import dependency_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan


def compile_fixed_text_variant_face(**arguments):
    """Keep the paired-body source producers together under one compiler port."""
    from .awaken_spell_faces import compile_fixed_awaken_face
    from .cleave_spell_faces import compile_fixed_cleave_face
    from .overload_spell_faces import compile_fixed_overload_face
    for lower in (compile_fight_excess_red_face,compile_fixed_awaken_face,compile_fixed_cleave_face,compile_fixed_overload_face):
        face=lower(**arguments)
        if face is not None:return face
    return None


def missing_paired_variant_residuals(ir, programs, kind):
    ability_ids = {program.ability_id for program in programs}
    return [{'face_id': face.face_id, 'residual_id': 'program_lowering:' + node.node_id,
        'kind': 'program_lowering', 'material': True, 'text': node.text, 'span': asdict(node.span),
        'reason': 'Recognized derived spell body has no executable correlated program',
        'blockers': ['complete paid spell variant lowering']}
        for face in ir.faces for node in face.nodes
        if node.kind == 'spell_variant' and kind.boundary in node.runtime_coverage
        and f'spell:{face.face_id}:{kind.value}' not in ability_ids]


def paired_spell_face(*, kind, face_id, face_name, oracle_text, material_rows, variants, spec,
        trusted_mechanics, capability_registry, capability_profile, residuals):
    if not isinstance(kind, SpellTextVariantKind) or len(variants) != 2:
        raise ValueError('Paired spell assembly requires one represented text variant')
    if any(not template or not effects or not mechanics for template, effects, targets, mechanics in variants):
        return None
    if any('modes' in schema for _, _, schema, _ in variants if schema is not None):
        return None
    gates = [dependency_gate(mechanics=mechanics, effects=effects, target_schema=targets,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=(kind.capability,),
        cost_schema={kind.value:spec.to_dict()} if kind is SpellTextVariantKind.AWAKEN else None)
        for _, effects, targets, mechanics in variants]
    if any(gate.blockers or gate.closure is None or len(gate.capabilities) < 2 for gate in gates):
        return None
    span = SourceSpan(material_rows[0][2].start, material_rows[-1][2].end, material_rows[0][2].line)
    nodes = []
    for index, ((template, effects, targets, mechanics), gate) in enumerate(zip(variants, gates, strict=True)):
        nodes.append(OracleNode(node_id=f'{face_id}:n1' if index == 0 else f'{face_id}:{kind.value}',
            kind='spell_ability' if index == 0 else 'spell_variant', text=oracle_text, span=span,
            active_zone='stack', event='resolve', lowerable=True, exact=True,
            template_id=template, effects=effects, target_schema=targets,
            cost={kind.value: spec.to_dict()}, mechanics=tuple(dict.fromkeys((*mechanics, kind.value))),
            runtime_coverage=(kind.boundary, kind.normal_role if index == 0 else kind.paid_role),
            capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable,
            capability_profile=gate.closure.profile, capability_fingerprint=gate.closure.fingerprint))
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        nodes=tuple(nodes), residuals=tuple(residuals))


def compile_fight_excess_red_face(*, record, face_id, face_name, oracle_text, material_rows,
                                 effect_template, trusted_mechanics, capability_registry,
                                 capability_profile, residuals):
    from ..rules.turn_retained_resolution_mana import (
        FIGHT_EXCESS_RED_CAPABILITY, FIGHT_EXCESS_RED_FIELD, TURN_POOL_COLOR_CAPABILITY, turn_pool_red_instruction,
    )

    fight_text = "Target creature you control fights target creature an opponent controls."
    expected = (
        fight_text + " If the creature the opponent controls is dealt excess damage this way, add that much {R}.",
        "Until end of turn, you don't lose unspent red mana as steps and phases end.",
    )
    if tuple(" ".join(row[1].split()).casefold() for row in material_rows) != tuple(text.casefold() for text in expected):
        return None
    template, effects, targets, mechanics = effect_template(fight_text, card_name=face_name or record.name)
    if template is None or len(effects) != 1:
        return None
    gate = dependency_gate(
        mechanics=mechanics, effects=effects, target_schema=targets, trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry, capability_profile=capability_profile,
        explicit_capabilities=(FIGHT_EXCESS_RED_CAPABILITY, TURN_POOL_COLOR_CAPABILITY, "mana.production.fixed_resolution"),
    )
    if gate.blockers or gate.closure is None:
        return None
    span = SourceSpan(material_rows[0][2].start, material_rows[-1][2].end, material_rows[0][2].line)
    node = OracleNode(
        node_id=f"{face_id}:n1", kind="spell_ability", text=oracle_text, span=span,
        active_zone="stack", event="resolve", lowerable=True, exact=True, template_id="fight-excess-red-retained-pool-v1",
        effects=({**effects[0], FIGHT_EXCESS_RED_FIELD: 1}, turn_pool_red_instruction()),
        target_schema=targets, mechanics=mechanics, runtime_coverage=(),
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable,
        capability_profile=gate.closure.profile, capability_fingerprint=gate.closure.fingerprint,
    )
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text, nodes=(node,), residuals=())
