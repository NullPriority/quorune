"""Both Ravenous components reuse dynamic entry counters and ordinary draw."""
import re
from ..ravenous_model import RAVENOUS_CAPABILITY,ravenous_condition
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .fixed_self_entry_counter_templates import dynamic_self_entry_counter_handler
from .static_runtime_nodes import runtime_handler_node
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,append_residual


def ravenous_keyword_nodes(*,record,node_id,line,material_line,span,printed_card_types,capability_registry,capability_profile,residuals):
    if not re.fullmatch(r'Ravenous\.?',material_line,re.I):return None
    if 'creature' not in {v.casefold() for v in printed_card_types} or '{X}' not in record.mana_cost:return None
    compiled=dynamic_self_entry_counter_handler('This creature enters with X +1/+1 counters on it.',source_name=record.name)
    if compiled is None:return None
    template,handler,caps=compiled
    entry=runtime_handler_node(node_id=node_id+':entry',line=line,span=span,compiled=(template,handler,(*caps,RAVENOUS_CAPABILITY)),
        kind='static_ability',event='zone.change',active_zone='all',runtime_coverage=(),mechanics=('ravenous',),
        dependency_reason='Ravenous entry requires its dynamic counter and entry-X contract',capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    gate=explicit_capabilities_gate((RAVENOUS_CAPABILITY,'zone.draw.library_to_hand','trigger.event.normalized_zone_change','trigger.placement.apnap'),capability_registry=capability_registry,capability_profile=capability_profile)
    rid=(append_residual(residuals,kind='dependency_contract',text=line,span=span,reason='Ravenous draw lacks trusted entry-X authority',blockers=gate.blockers),) if gate.blockers else ()
    draw=OracleNode(node_id=node_id+':draw',kind='triggered_ability',text=line,span=span,active_zone='battlefield',event='permanent.enter.self',
        event_condition=ravenous_condition(),lowerable=True,exact=not gate.blockers,template_id='ravenous-entry-draw-v1',
        effects=({'op':'draw','player':'$controller','count':1,'private':True},),runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,'intervening_condition','ravenous_entry_x'),
        mechanics=('ravenous','cr-603-handling-triggered-abilities'),residual_ids=rid,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return entry,draw


def unsupported_ravenous_node(*,node_id,line,span,mechanics,residuals):
    residual_id=append_residual(residuals,kind='keyword_grammar',text=line,span=span,
        reason='Ravenous requires its complete printed creature X-entry and draw production',blockers=('ravenous compound keyword grammar',))
    return OracleNode(node_id=node_id,kind='keyword_ability',text=line,span=span,active_zone='all',event='keyword',lowerable=False,exact=False,
        mechanics=mechanics,residual_ids=(residual_id,))
