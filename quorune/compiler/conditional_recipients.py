from __future__ import annotations

import re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..continuous_conditions import FixedPublicStateConditionKind as Kind, FixedPublicStateFact
from ..object_predicate import ObjectQuerySpec
from ..conditional_recipient_model import ConditionalRecipientSpec, CONDITIONAL_RECIPIENT_CAPABILITY
from .continuous_templates import _conditional_target
from .public_state_queries import fixed_public_state_parts
from .static_runtime_nodes import runtime_handler_node


def conditional_recipient_template(text: str, *, source_name: str):
    """Compose closed self/set recipients beneath one complete live condition."""
    parsed = fixed_public_state_parts(text, source_name=source_name)
    if parsed is None:
        return None
    condition, body = parsed
    unique = condition.terms and len(condition.terms) == 2 and all(
        term.kind in {Kind.QUERY_COUNT_AT_LEAST, Kind.QUERY_COUNT_AT_MOST}
        and term.amount == 1 and term.quantity is not None
        and term.quantity == CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=('battlefield',), types_all=('creature',))) for term in condition.terms)
    parts = None
    roles = None
    if unique and re.match(r'^that creature (?:gets|has) ', body, re.I):
        parts = [re.sub(r'^that creature', 'Creatures you control', body, count=1, flags=re.I)
            .replace(' gets ', ' get ', 1).replace(' and has ', ' and have ', 1)]
        roles = ['unique_creature']
    joined = re.fullmatch(r'(?P<self>This creature|This permanent) and (?P<set>.+ you control) get (?P<stats>[+-]\d+/[+-]\d+)\.?', body, re.I)
    if joined is not None:
        parts = [joined['self']+' gets '+joined['stats']+'.',
            'Other '+joined['set']+' get '+joined['stats']+'.']
        if joined['set'].casefold() != 'land creatures you control':
            return None
        roles = ['source', 'other_land_creatures']
    split = re.fullmatch(r'(?P<self>This creature gets [+-]\d+/[+-]\d+) and (?P<set>(?:other )?creatures you control (?:get|have) .+)', body, re.I)
    if split is not None:
        parts = [split['self']+'.', split['set']]
        roles = ['source', 'other_creatures' if split['set'].casefold().startswith('other ') else 'controlled_creatures']
    if parts is None:
        return None
    compiled = [_conditional_target(part, source_name=source_name) for part in parts]
    if any(value is None for value in compiled):
        return None
    handlers = tuple({'handler_id': 'continuous.characteristics.fixed-public-state.v1', 'schema_version': 1,
        'event': 'characteristics.evaluate', 'source_condition': condition.to_dict(),
        'target': dict(target), 'modifier': dict(modifier), 'recipient': ConditionalRecipientSpec(role).to_dict()}
        for (target, modifier, _caps), role in zip(compiled, roles))
    caps = {CONDITIONAL_RECIPIENT_CAPABILITY, 'continuous.characteristics.fixed_public_state',
        *(cap for _target, _modifier, dependencies in compiled for cap in dependencies)}
    if any(leaf.fact is FixedPublicStateFact.CONTROLLER_CONTROLS_OWN_COMMANDER for leaf in condition.leaf_conditions()):
        caps.add('state_query.commander.designation')
    return 'continuous-conditional-recipients-v1', handlers, tuple(sorted(caps))


def conditional_recipient_node(*, node_id, line, material_line, span, source_name,
        capability_registry, capability_profile, residuals):
    compiled = conditional_recipient_template(material_line, source_name=source_name)
    if compiled is None:
        return None
    return runtime_handler_node(node_id=node_id, line=line, span=span, compiled=compiled,
        kind='static_ability', event='characteristics.evaluate', runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Conditional recipient composition requires its complete source and current condition owners',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
