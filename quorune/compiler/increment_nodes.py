from __future__ import annotations

"""Printed Increment instances reuse the ordinary counter trigger owner."""

from ..increment_model import CAPABILITY,CONDITION_FIELD
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,append_residual


def increment_keyword_node(*,node_id,line,material_line,span,mechanics,capability_registry,capability_profile,residuals):
    instances=tuple(part.strip().casefold() for part in material_line.rstrip('.').split(','))
    if not instances or any(part!='increment' for part in instances) or set(mechanics)!={'increment'}:
        return None
    gate=explicit_capabilities_gate((CAPABILITY,'counter.producer.fixed_effect','trigger.event.normalized_spell_cast','trigger.placement.apnap'),capability_registry=capability_registry,capability_profile=capability_profile)
    ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Increment requires committed cast mana and current-size intervening checks',blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,active_zone='battlefield',event='spell.cast',
        lowerable=True,exact=not ids,template_id='increment-paid-cast-counter-v1',
        effects=({'op':'place_counters','card':'$source.zone_object','counter':'+1/+1','amount':1,'source':'$source'},),
        event_condition={'field':CONDITION_FIELD,'op':'truthy'},runtime_coverage=('intervening_condition',CURRENT_ABILITY_FRAGMENT_COVERAGE),
        mechanics=tuple('increment' for _ in instances),residual_ids=ids,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
