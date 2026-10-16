from __future__ import annotations

"""A printed Offspring instance owns separate cost and entry-trigger nodes."""

import re

from ..paid_entry_copy_model import (
    PaidEntryCopySpec, PAID_ENTRY_COPY_CAPABILITY, PAID_ENTRY_COPY_HANDLER,
    PAID_ENTRY_COPY_MECHANIC,
    SQUAD_ENTRY_COPY_CAPABILITY, SQUAD_ENTRY_COPY_MECHANIC, SQUAD_ENTRY_COPY_QUANTITY,
)
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..util import mana_cost_to_vector
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node
from .token_copy_templates import token_copy_recipe_template


def paid_entry_copy_keyword_nodes(*, record, node_id, line, material_line, span,
        mechanics, capability_registry, capability_profile, residuals, trusted_mechanics, **_unused):
    if len(mechanics or ()) != 1 or mechanics[0] not in {'offspring', 'squad'}:
        return None
    keyword = mechanics[0]
    capability = SQUAD_ENTRY_COPY_CAPABILITY if keyword == 'squad' else PAID_ENTRY_COPY_CAPABILITY
    match = re.fullmatch(keyword + r' (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\.?', material_line.strip(), re.I)
    count = len(re.findall(r'^(?:Offspring|Squad)\b', record.oracle_text, re.M | re.I))
    try:
        if match is None or count != 1 or record.layout != 'normal' or record.faces:
            raise ValueError('Offspring needs one printed fixed ordinary-mana instance on a normal face')
        requirements, complex_symbols = mana_cost_to_vector(match['cost'])
        if complex_symbols:
            raise ValueError('Offspring cost has unsupported mana symbols')
        spec = PaidEntryCopySpec(node_id, requirements, keyword=keyword)
    except (ValueError, TypeError):
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Paid entry copy cost or instance identity is outside the closed grammar',
            blockers=('single positive fixed ordinary-mana Offspring instance',))
        return (OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span,
            active_zone='all', event='unresolved', lowerable=False, exact=False,
            mechanics=mechanics, residual_ids=(rid,)),)
    cost = runtime_handler_node(node_id=node_id + ':cost', line=line, span=span,
        compiled=('paid-entry-copy-cost-v1', {'handler_id': PAID_ENTRY_COPY_HANDLER,
            'schema_version': 1, 'event': 'cast.cost', 'paid_copy': spec.to_dict(),
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True}, (capability, PAID_ENTRY_COPY_CAPABILITY)),
        kind='keyword_ability', event='cast.cost', active_zone='all',
        runtime_coverage=('fixed_additional_entry_copy_cost',),
        dependency_reason='Offspring requires admitted payment and paid-entry copy ownership',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    template, effects, schema, result_mechanics = token_copy_recipe_template(
        "Create a token that's a copy of this creature" + ("." if keyword == 'squad' else ", except it's 1/1."),
        source_name=record.name, source_is_permanent=True).compiled()
    if keyword == 'squad':
        effects = ({**effects[0], 'quantity': SQUAD_ENTRY_COPY_QUANTITY},)
    entry_mechanics = (SQUAD_ENTRY_COPY_MECHANIC if keyword == 'squad' else PAID_ENTRY_COPY_MECHANIC, 'cr-603-handling-triggered-abilities',
        'fixed-typed-event-effect-trigger', *result_mechanics)
    gate = dependency_gate(mechanics=entry_mechanics, effects=effects, target_schema=schema,
        trusted_mechanics=trusted_mechanics, explicit_capabilities=(capability,),
        capability_registry=capability_registry, capability_profile=capability_profile)
    ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Paid entry copy needs its payment, occurrence and canonical copy owners', blockers=gate.blockers),) if gate.blockers else ()
    entry = OracleNode(node_id=node_id + ':entry', kind='triggered_ability', text=line,
        span=span, active_zone='battlefield', event='permanent.enter.self',
        event_condition={'field': 'paid_entry_copy_instance', 'op': 'eq', 'value': spec.instance_id},
        lowerable=True, exact=not ids, template_id=template,
        effects=effects, target_schema=schema, mechanics=entry_mechanics,
        residual_ids=ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return cost, entry
