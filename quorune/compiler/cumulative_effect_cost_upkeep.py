"""Source-spanned beneficial actions used as complete cumulative upkeep costs."""
from ..cumulative_effect_cost_upkeep import compile_effect_cost_upkeep
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..rules.cumulative_object_upkeep import CONTROLLER_UPKEEP
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,append_residual


def cumulative_effect_cost_upkeep_node(*,node_id,line,material_line,span,mechanics,capability_registry,capability_profile,residuals):
    if mechanics!=('cumulative upkeep',):return None
    spec=compile_effect_cost_upkeep(material_line)
    if spec is None:return None
    gate=explicit_capabilities_gate(spec.capabilities,capability_registry=capability_registry,capability_profile=capability_profile)
    blocked=() if not gate.blockers else (append_residual(residuals,kind='dependency_contract',text=line,span=span,reason='Beneficial upkeep cost lacks its complete cost/source ownership',blockers=gate.blockers),)
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,active_zone='battlefield',event='step.begin',event_condition=CONTROLLER_UPKEEP,
        lowerable=True,exact=not blocked,template_id='cumulative-upkeep-beneficial-cost-v1',effects=(spec.effect(),),mechanics=(*mechanics,FIXED_EFFECT_PAYMENT_MECHANIC),residual_ids=blocked,
        capability_dependencies=gate.capabilities,capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
