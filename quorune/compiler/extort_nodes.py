from __future__ import annotations
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.extort import EXTORT_CAPABILITY,EXTORT_OPERATION
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode,append_residual


def extort_keyword_node(*,node_id,line,material_line,span,mechanics,capability_registry,capability_profile,residuals):
    if mechanics!=('extort',) or material_line.strip().casefold()!='extort':return None
    gate=explicit_capability_gate(EXTORT_CAPABILITY,capability_registry=capability_registry,capability_profile=capability_profile)
    ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,reason='Extort requires trusted hybrid payment and ordered actual-life transfer',blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,active_zone='battlefield',event='spell.cast',
        lowerable=True,exact=not gate.blockers,template_id='extort-cast-payment-transfer-v1',
        effects=({'op':EXTORT_OPERATION,'schema_version':1,'player':'$controller','source':'$source','stage':'pay'},),
        target_schema=None,mechanics=('extort','cr-603-handling-triggered-abilities'),
        event_condition={'field':'controller','op':'eq','value':'$source.controller'},
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),residual_ids=ids,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
