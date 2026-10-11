from __future__ import annotations

from ..ability_fragments import ability_fragment_to_dict,CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..enlist_model import EnlistSpec,ENLIST_CAPABILITY,ENLIST_HANDLER
from .static_runtime_nodes import runtime_handler_node
from .ir_model import OracleNode
from .dependency_gate import explicit_capabilities_gate
from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin


def enlist_keyword_node(**values):
    if values['mechanics']!=('enlist',) or values['material_line'].casefold().rstrip('.')!='enlist':return None
    return runtime_handler_node(node_id=values['node_id'],line=values['line'],span=values['span'],
        compiled=('printed-enlist-attack-cost-v1',{'handler_id':ENLIST_HANDLER,'schema_version':1,'event':'characteristics.evaluate','fragment':ability_fragment_to_dict(EnlistSpec())},ENLIST_CAPABILITY),
        kind='keyword_ability',event='characteristics.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Enlist requires eligible optional attack taps, linked reflexive trigger placement and live or last-known power.',capability_registry=values['capability_registry'],capability_profile=values['capability_profile'],residuals=values['residuals'])


def enlist_reflexive_node(**values):
    gate=explicit_capabilities_gate((ENLIST_CAPABILITY,),capability_registry=values['capability_registry'],capability_profile=values['capability_profile'])
    amount=ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_OBJECT,characteristic='power').to_dict()
    return OracleNode(node_id=values['node_id']+':enlist-trigger',kind='triggered_ability',text=values['line'],span=values['span'],active_zone='battlefield',event='combat.enlist.reflexive',lowerable=True,exact=not gate.blockers,
        template_id='printed-enlist-reflexive-growth-v1',effects=({'op':'modify_stats_until_end_of_turn','card':'$source.zone_object','power':amount,'toughness':0},),
        mechanics=('enlist','scalar-effect-amount','cr-611-continuous-effects'),runtime_coverage=('enlist_reflexive_growth',),capability_dependencies=gate.capabilities,capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
