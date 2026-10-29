"""Source-pinned optional Bargain and linked self-entry results."""
import re
from ..bargain_model import BARGAIN_CAPABILITY, BARGAIN_HANDLER, FixedBargainSpec
from ..declared_cast_cost_model import DECLARED_CAST_COST_CAPABILITY, DECLARED_CAST_COST_MECHANIC
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..characteristic_evaluation import type_parts
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node
from .dependency_gate import dependency_gate


def printed_bargain_spec(record):
    types, _, _ = type_parts(record.type_line)
    rows = [row.strip() for row in record.oracle_text.splitlines() if re.match(r'^Bargain\b', row.strip(), re.I)]
    if (record.layout != 'normal' or record.faces or 'land' in types or len(rows) != 1
            or re.fullmatch(r'Bargain(?:\s+\([^()]*\))?\.?', rows[0], re.I) is None
            or re.search(r'^(?:Casualty|Conspire|Convoke|Improvise|Delve|Emerge|Kicker|Multikicker|Replicate|As an additional cost)\b', record.oracle_text, re.I | re.M)):
        return None
    return FixedBargainSpec()


def bargain_static_node(*, record, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    spec = printed_bargain_spec(record)
    if spec is None:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Bargain requires one complete printed instance with independently represented prices and linked results',
            blockers=('multiple/granted instances, unrepresented layouts and mixed consuming costs',))
        return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span, active_zone='all', event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))
    descriptor = {'handler_id': BARGAIN_HANDLER, 'schema_version': 1, 'event': 'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'bargain': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('printed-bargain-declaration-v1', descriptor, (BARGAIN_CAPABILITY,)), kind='keyword_ability', event='cast.cost', active_zone='all',
        dependency_reason='Bargain requires its sacrifice union and linked declaration facts', capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)


def bargain_entry_trigger_node(*, bargain_spec, node_id, line, material_line, span, card_name, effect_template,
        trusted_mechanics, capability_registry, capability_profile, residuals):
    if bargain_spec is None:return None
    match = re.fullmatch(r'When (?P<source>.+?) enters, if it was bargained, (?P<body>.+)', material_line.strip(), re.I)
    if match is None or not (source_self_permanent_type(match['source']) is not None or SourceReferenceSpec(card_name).matches(match['source'])):return None
    body = match['body']
    if re.match(r'it (?:deals|gets|gains) ', body, re.I):body = card_name + body[2:]
    template, effects, schema, result_ids = effect_template(body, card_name=card_name)
    if template is None:return None
    mechanics = ('cr-603-handling-triggered-abilities', 'fixed-typed-event-effect-trigger', DECLARED_CAST_COST_MECHANIC, *result_ids)
    gate = dependency_gate(mechanics=mechanics, effects=effects, target_schema=schema, trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry, capability_profile=capability_profile,
        explicit_capabilities=(BARGAIN_CAPABILITY, DECLARED_CAST_COST_CAPABILITY))
    ids = ()
    if gate.blockers:ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Bargained entry needs linked declaration facts and independently complete results', blockers=gate.blockers),)
    closure = gate.closure
    return OracleNode(node_id=node_id, kind='triggered_ability', text=line, span=span, active_zone='battlefield', event='permanent.enter.self',
        event_condition={'field': 'declared_cast_cost_instances', 'op': 'contains_any', 'value': [bargain_spec.instance_id]},
        effects=effects, target_schema=schema, mechanics=mechanics, template_id='linked-declared-cast-cost-entry-v1', lowerable=True, exact=not ids,
        residual_ids=ids, capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None, capability_fingerprint=closure.fingerprint if closure else None)
