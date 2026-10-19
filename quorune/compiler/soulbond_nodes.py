"""The two independently identified triggered abilities of Soulbond."""
from __future__ import annotations

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..soulbond_model import (
    SOULBOND_CAPABILITY, SOULBOND_CHOICE_OPERATION, SOULBOND_COVERAGE,
    SOULBOND_SELF_CONDITION, SOULBOND_OTHER_CONDITION,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def soulbond_keyword_nodes(*, node_id, line, span, instances, capability_registry, capability_profile, residuals):
    gate = explicit_capabilities_gate((SOULBOND_CAPABILITY,),
        capability_registry=capability_registry, capability_profile=capability_profile)
    ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Soulbond requires its typed pair, choice and lifecycle owners', blockers=gate.blockers),) if gate.blockers else ()
    nodes = []
    for instance in range(instances):
        for other in (False, True):
            conditions = [{'field':SOULBOND_OTHER_CONDITION if other else SOULBOND_SELF_CONDITION, 'op':'truthy'}]
            if other:
                conditions.extend(({'field':'card','op':'ne','value':'$source.ref'},
                    {'field':'controller','op':'eq','value':'$source.controller'},
                    {'field':'types','op':'contains_any','value':['creature']}))
            nodes.append(OracleNode(
                node_id=f'{node_id}:soulbond:{instance + 1}:{"other" if other else "self"}',
                kind='triggered_ability', text=line, span=span, active_zone='battlefield',
                event='permanent.enter' if other else 'permanent.enter.self', lowerable=True,
                exact=not ids, template_id='soulbond-other-entry-v1' if other else 'soulbond-own-entry-v1',
                event_condition={'all':conditions}, effects=({'op':SOULBOND_CHOICE_OPERATION,
                    'player':'$controller','source_ref':'$source','source_identity':'$context.source_logical_object_id',
                    'mode':'other_entry' if other else 'source_entry',
                    'entered_ref':'$context.card' if other else None,
                    'entered_identity':'$context.card_object_identity' if other else None},),
                runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE, SOULBOND_COVERAGE, 'intervening_condition'),
                mechanics=('soulbond','cr-603-handling-triggered-abilities'), residual_ids=ids,
                capability_dependencies=gate.capabilities,
                capability_closure=gate.closure.reachable if gate.closure else (),
                capability_profile=gate.closure.profile if gate.closure else None,
                capability_fingerprint=gate.closure.fingerprint if gate.closure else None))
    return tuple(nodes)


def soulbond_node_group(*, compile_sibling, **values):
    mechanics = values['mechanics']
    if 'soulbond' not in mechanics:
        return None
    bonded = soulbond_keyword_nodes(node_id=values['node_id'], line=values['line'], span=values['span'],
        instances=mechanics.count('soulbond'), capability_registry=values['capability_registry'],
        capability_profile=values['capability_profile'], residuals=values['residuals'])
    siblings = tuple(mechanic for mechanic in mechanics if mechanic != 'soulbond')
    if not siblings:
        return bonded
    keys = ('record','face_id','line','span','printed_power','trusted_mechanics',
            'capability_registry','capability_profile','residuals')
    sibling = compile_sibling(**{key:values[key] for key in keys},
        node_id=values['node_id'] + ':non-soulbond', material_line=', '.join(siblings), mechanics=siblings)
    return (sibling, *bonded)
