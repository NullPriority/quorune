"""Closed complete intrinsic Gravestorm keyword and printed reminder."""
from . import regex_working_set as re

_REMINDER = re.compile(
    r'When you cast this spell, copy it for each permanent put into a graveyard'
    r'(?: from the battlefield)? this turn\.'
    r'(?: You may choose new targets for the copies\.)?', re.I)


def is_gravestorm_line(line):
    text = ' '.join(line.strip().split())
    match = re.fullmatch(r'Gravestorm(?: \((?P<reminder>[^()]+)\))?\.?', text, re.I)
    return bool(match is not None and (match['reminder'] is None or _REMINDER.fullmatch(match['reminder'])))


def gravestorm_keyword_node(*, node_id, line, material_line, span, capability_registry,
        capability_profile, residuals, mechanics=()):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..rules.gravestorm import GRAVESTORM_DEPENDENCIES, gravestorm_instruction, gravestorm_condition
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode, append_residual
    if mechanics != ('gravestorm',) or not is_gravestorm_line(line) or not is_gravestorm_line(material_line):
        return None
    gate = explicit_capabilities_gate(GRAVESTORM_DEPENDENCIES,
        capability_registry=capability_registry, capability_profile=capability_profile)
    blocked = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Gravestorm requires the complete permanent-death journal and canonical cast-spell copies',
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(node_id=node_id, kind='triggered_ability', text=line, span=span,
        active_zone='stack', event='spell.cast', event_condition=gravestorm_condition(),
        lowerable=True, exact=not blocked, template_id='gravestorm-intrinsic-cast-v1',
        effects=(gravestorm_instruction(),), target_schema=None,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=('gravestorm', 'trigger-event-normalized-spell-cast', 'cr-603-handling-triggered-abilities'),
        residual_ids=blocked, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
