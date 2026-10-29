from __future__ import annotations

"""Source-spanned state conditions with the existing mandatory source sacrifice."""

from dataclasses import replace
import re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..state_trigger_model import PublicStateTriggerSpec,STATE_TRIGGER_EVENT,STATE_TRIGGER_MECHANIC
from ..source_maintenance import SourceMaintenanceSpec
from ..rules.state_triggers import state_trigger_capabilities
from ..characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope
from ..continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec
from .public_state_queries import fixed_public_state_condition
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,append_residual


def _state_condition(text,source_name):
    condition=fixed_public_state_condition(text,source_name=source_name)
    if condition is None and text.casefold().startswith('you control no other '):
        condition=fixed_public_state_condition(re.sub('no other ','no ',text,count=1,flags=re.I),source_name=source_name)
        if condition is not None and condition.quantity is not None:
            condition=replace(condition,quantity=replace(condition.quantity,exclude_source=True))
    if condition is None and text.casefold()=='you control no snow lands':
        condition=FixedPublicStateConditionSpec(kind=FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST,amount=0,schema_version=2,
            quantity=CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                query=ObjectQuerySpec(zones=('battlefield',),types_any=('land',),supertypes_all=('snow',))))
    return condition


def public_state_trigger_node(*,node_id,line,material_line,span,card_name,type_line,
        capability_registry,capability_profile,residuals):
    match=re.fullmatch(r'When (?P<condition>you control .+?), sacrifice (?P<source>.+)\.',material_line,re.I)
    if match is None or not re.search(r'\b(?:creature|artifact|enchantment|land|planeswalker|battle)\b',type_line,re.I):return None
    source=match['source']
    if not re.fullmatch(r'this (?:creature|artifact|enchantment|land|permanent)',source,re.I) and not SourceReferenceSpec(card_name).matches(source):return None
    condition=_state_condition(match['condition'],card_name)
    if condition is None:return None
    try:spec=PublicStateTriggerSpec(condition)
    except ValueError:return None
    gate=explicit_capabilities_gate(state_trigger_capabilities(spec),capability_registry=capability_registry,capability_profile=capability_profile)
    residual_ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Public-state trigger lacks its trusted state observation or sacrifice owner',blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,active_zone='battlefield',
        event=STATE_TRIGGER_EVENT,lowerable=True,exact=not gate.blockers,template_id='public-state-source-sacrifice-v1',
        effects=(SourceMaintenanceSpec().effect(),),event_condition=spec.event_condition(),runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=(STATE_TRIGGER_MECHANIC,'fixed-source-maintenance','cr-603-handling-triggered-abilities'),
        residual_ids=residual_ids,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
