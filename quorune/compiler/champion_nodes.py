"""The two linked triggered abilities in a complete Champion keyword line."""
from __future__ import annotations

from ..champion_model import CHAMPION_CAPABILITY, CHAMPION_CHOICE_OPERATION
from .champion_templates import champion_spec
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def champion_keyword_nodes(*, record, face_id, node_id, line, material_line, span,
                           capability_registry, capability_profile, residuals):
    spec=champion_spec(material_line.strip().rstrip('.'))
    if spec is None:
        return None
    gate=explicit_capabilities_gate((CHAMPION_CAPABILITY,),capability_registry=capability_registry,
                                    capability_profile=capability_profile)
    ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Champion requires its typed choice, linked exile and explicit return owners',blockers=gate.blockers),) if gate.blockers else ()
    pair=f'champion:{record.oracle_id}:{face_id}:n{span.line}'
    result=[]
    for entry in (True,False):
        effect=({'op':CHAMPION_CHOICE_OPERATION,'ability_pair':pair,'spec':spec.to_dict()} if entry else
                {'op':'return_champion_exile','ability_pair':pair})
        result.append(OracleNode(node_id=f'{node_id}:champion:{"enter" if entry else "leave"}',
            kind='triggered_ability',text=line,span=span,active_zone='battlefield',
            event='permanent.enter.self' if entry else 'permanent.leave.self',lowerable=True,exact=not ids,
            template_id='champion-entry-v1' if entry else 'champion-leave-v1',effects=(effect,),
            runtime_coverage=('identity_pinned_champion',),mechanics=('champion','cr-603-handling-triggered-abilities'),
            residual_ids=ids,capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable if gate.closure else (),
            capability_profile=gate.closure.profile if gate.closure else None,
            capability_fingerprint=gate.closure.fingerprint if gate.closure else None))
    return tuple(result)
