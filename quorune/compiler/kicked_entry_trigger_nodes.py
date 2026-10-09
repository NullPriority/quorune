from __future__ import annotations

"""Kicked self-entry triggers over sealed zone-occurrence cast facts."""

import re
from typing import Any

from ..rules.capabilities import CapabilityRegistry
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


KICKED_ENTRY_TRIGGER_CAPABILITY = 'trigger.entry.fixed_kicked_result'
KICKED_ENTRY_TRIGGER_MECHANIC = 'fixed-kicked-entry-trigger'
_TRIGGER = re.compile(r'When (?P<source>.+?) enters, if it was kicked, (?P<body>.+)', re.I)


def fixed_source_entry_or_step_trigger_node(**kwargs: Any) -> OracleNode | None:
    """Route the two closed source-bound productions with original precedence."""
    from .source_maintenance_nodes import source_maintenance_node
    maintenance = source_maintenance_node(**{
        key: value for key, value in kwargs.items() if key not in {'effect_template', 'trusted_mechanics'}
    })
    return maintenance if maintenance is not None else fixed_kicked_entry_trigger_node(**kwargs)


def fixed_kicked_entry_trigger_node(
    *, node_id: str, line: str, material_line: str, span: SourceSpan,
    card_name: str, effect_template: Any, trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None, capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    match = _TRIGGER.fullmatch(material_line.strip())
    if match is None or not (
        source_self_permanent_type(match['source']) is not None
        or SourceReferenceSpec(card_name).matches(match['source'])
    ):
        return None
    body = match['body']
    if re.match(r'it (?:deals|gets|gains) ', body, re.I):
        body = re.sub(r'^it ', card_name + ' ', body, flags=re.I)
    template, effects, target_schema, result_mechanics = effect_template(body, card_name=card_name)
    if template is None:
        return None
    mechanics = ('cr-603-handling-triggered-abilities', KICKED_ENTRY_TRIGGER_MECHANIC,
        'fixed-typed-event-effect-trigger', *result_mechanics)
    gate = dependency_gate(
        mechanics=mechanics, effects=effects, target_schema=target_schema,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile,
        explicit_capabilities=(KICKED_ENTRY_TRIGGER_CAPABILITY,),
    )
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(
            residuals, kind='dependency_contract', text=line, span=span,
            reason='Kicked entry requires sealed cast facts and independently closed result owners',
            blockers=gate.blockers,
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind='triggered_ability', text=line, span=span,
        active_zone='battlefield', event='permanent.enter.self',
        event_condition={'field': 'cast_option', 'op': 'eq', 'value': 'kicked'},
        lowerable=True, exact=not residual_ids, template_id='fixed-kicked-entry-trigger-v1',
        effects=effects, target_schema=target_schema, mechanics=mechanics,
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
