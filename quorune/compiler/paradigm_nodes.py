"""Compose the Paradigm keyword only around one independently complete spell."""
from dataclasses import dataclass, replace
from ..paradigm_model import PARADIGM_HANDLER, PARADIGM_MECHANIC
from ..rules.paradigm import PARADIGM_CAPABILITIES, PARADIGM_TEMPLATE
from ..rules.spell_self_exile import SELF_EXILE
from .dependency_gate import explicit_capabilities_gate
from .ir_model import SourceSpan, append_residual


@dataclass(frozen=True, slots=True)
class ParadigmCompilationContext:
    row: tuple


def paradigm_source_rows(rows):
    indices = [i for i, (_line, material, _span) in enumerate(rows) if material.casefold() == 'paradigm']
    if len(indices) != 1 or indices[0] != len(rows) - 1:
        return rows, None
    index = indices[0]
    return tuple(row for i, row in enumerate(rows) if i != index), ParadigmCompilationContext(rows[index])


def apply_paradigm_context(face, context, *, capability_registry, capability_profile):
    if context is None:
        return face
    line, _material, span = context.row
    nodes = list(face.nodes); residuals = list(face.residuals)
    spells = [i for i, node in enumerate(nodes) if node.kind == 'spell_ability']
    complete = len(spells) == 1 and len(nodes) == 1 and nodes[spells[0]].exact and not face.residuals
    required = (*PARADIGM_CAPABILITIES, *(nodes[spells[0]].capability_dependencies if spells else ()))
    gate = explicit_capabilities_gate(required, capability_registry=capability_registry, capability_profile=capability_profile)
    if not complete or gate.blockers:
        append_residual(residuals, kind='dependency_contract', text=line, span=span,
            reason='Paradigm requires one complete spell body and its first-resolution copy lifecycle',
            blockers=gate.blockers or ('complete spell body',))
    if spells:
        slot = spells[0]; node = nodes[slot]
        combined = SourceSpan(node.span.start, span.end, node.span.line)
        nodes[slot] = replace(node, text=face.oracle_text[combined.start:combined.end], span=combined,
            template_id=PARADIGM_TEMPLATE + '+' + SELF_EXILE.template + '+' + str(node.template_id),
            handlers=(*node.handlers, {'handler_id': PARADIGM_HANDLER, 'schema_version': 1, 'event': 'resolve'}),
            mechanics=tuple(dict.fromkeys((*node.mechanics, PARADIGM_MECHANIC, SELF_EXILE.mechanic))),
            runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage, PARADIGM_MECHANIC, SELF_EXILE.mechanic))),
            exact=complete and not gate.blockers, capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable if gate.closure else (),
            capability_profile=gate.closure.profile if gate.closure else None,
            capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return replace(face, nodes=tuple(nodes), residuals=tuple(residuals))
