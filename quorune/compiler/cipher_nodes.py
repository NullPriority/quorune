"""Compose Cipher after a complete ordinary spell body and declare its exile grant."""
from dataclasses import dataclass, replace
from ..cipher_model import CIPHER_CAPABILITY, CIPHER_ENCODE_OPERATION, CIPHER_GRANT_HANDLER, CIPHER_HANDLER
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


@dataclass(frozen=True, slots=True)
class CipherCompilationContext:
    row: tuple
    row_index: int


def cipher_source_rows(record, rows):
    indices = [i for i, (_line, material, _span) in enumerate(rows) if material.casefold() == 'cipher']
    if not indices or len(indices) != 1 or indices[0] != len(rows) - 1:
        return rows, None
    index = indices[0]
    return tuple(row for i, row in enumerate(rows) if i != index), CipherCompilationContext(rows[index], index)


def contextual_source_rows(record, rows):
    from .station_context_nodes import station_source_context
    rows, station = station_source_context(record, rows)
    rows, cipher = cipher_source_rows(record, rows)
    return rows, station, cipher


def apply_cipher_context(face, context, *, capability_registry, capability_profile):
    if context is None:
        return face
    nodes = list(face.nodes)
    residuals = list(face.residuals)
    spells = [i for i, node in enumerate(nodes) if node.kind == 'spell_ability']
    line, _material, span = context.row
    gate = explicit_capabilities_gate((CIPHER_CAPABILITY, 'continuous.ability.fixed_query_grant'), capability_registry=capability_registry, capability_profile=capability_profile)
    extra = ()
    if gate.blockers or not spells:
        extra = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
            reason='Cipher requires a complete spell body and trusted encoding lifecycle', blockers=gate.blockers or ('complete spell body',)),)
    if spells:
        slot = spells[-1]
        node = nodes[slot]
        child_gate = explicit_capabilities_gate((*node.capability_dependencies, CIPHER_CAPABILITY),
            capability_registry=capability_registry, capability_profile=capability_profile)
        nodes[slot] = replace(node, effects=(*node.effects, {'op': CIPHER_ENCODE_OPERATION}),
            handlers=(*node.handlers, {'handler_id': CIPHER_HANDLER, 'schema_version': 1, 'event': 'resolve'}),
            exact=node.exact and not child_gate.blockers, capability_dependencies=child_gate.capabilities,
            capability_closure=child_gate.closure.reachable if child_gate.closure else (),
            capability_profile=child_gate.closure.profile if child_gate.closure else None,
            capability_fingerprint=child_gate.closure.fingerprint if child_gate.closure else None,
            runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage, 'cipher_spell_resolution'))))
    nodes.append(OracleNode(node_id=face.face_id + ':cipher-exile', kind='static_ability', text=line, span=span,
        active_zone='exile', event='characteristics.evaluate', lowerable=True, exact=not extra,
        template_id='cipher-exile-grant-v1', handlers=({'handler_id': CIPHER_GRANT_HANDLER, 'schema_version': 1,
            'event': 'characteristics.evaluate'},), mechanics=('cipher',), residual_ids=extra,
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None))
    return replace(face, nodes=tuple(nodes), residuals=tuple(residuals))


def finish_contextual_face(context, *args):
    from .leveler_context_nodes import leveler_face_ir
    return apply_cipher_context(leveler_face_ir(*args), context,
        capability_registry=args[-2], capability_profile=args[-1])
