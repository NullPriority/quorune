from __future__ import annotations

"""Source-pinned precedence for redundant reviewed search and upkeep views."""

from collections.abc import Mapping
from ..continuous_conditions import FixedPublicStateConditionKind,FixedPublicStateConditionSpec
from ..characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..rules.library_search_capability_shapes import fixed_type_to_hand_search_node_capabilities
from ..compiler.activated_costs import activated_ability_cost
from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors


def _same_current_source(record,current,old):
    return (current.oracle_id==old.oracle_id==record.oracle_id and current.trust_level==old.trust_level=='trusted'
        and current.capability_closure and current.capability_closure.get('trusted') is True
        and not str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
        and all(current.provenance.get(key) and current.provenance[key]==old.provenance.get(key)
            for key in ('source_oracle_hash','source_rulings_hash'))
        and all(getattr(current,key)==getattr(old,key) for key in
            ('event','active_zone','target_schema','destination','requires_arbiter')))


def _catalog_preserves_cost(current,old):
    if current.handlers!=old.handlers or len(current.handlers)!=1:return False
    descriptor=current.handlers[0]
    if descriptor.get('handler_id')!='activation.catalog.pinned.v1':return False
    ability=descriptor.get('ability');cost=current.cost_schema
    if not isinstance(ability,Mapping) or not isinstance(cost,Mapping) or ability.get('uncompiled_costs'):return False
    try:parsed=activated_abilities_from_descriptors(current.handlers)
    except (TypeError,ValueError):return False
    if len(parsed)!=1 or thaw_value(cost)!=activated_ability_cost(parsed[0]):return False
    return old.cost_schema is None or old.cost_schema==cost


def shadowed_reviewed_fixed_search_keys(record,generated_programs,reviewed_programs):
    shadowed=set();reviewed={p.key:p for p in reviewed_programs}
    for current in generated_programs:
        old=reviewed.get(current.key)
        if (old is None or not _same_current_source(record,current,old) or current.event!='activate'
                or current.event_condition!=old.event_condition or current.target_schema is not None
                or not _catalog_preserves_cost(current,old) or len(current.effects)!=len(old.effects)):
            continue
        dependencies=fixed_type_to_hand_search_node_capabilities(effects=current.effects,
            target_schema=None,mechanic_ids=current.coverage)
        if not dependencies or not set(dependencies)<=set(current.capability_dependencies):continue
        previous=[dict(e) for e in old.effects]
        if any(e.get('searching_player','$controller')!='$controller' for e in previous):continue
        for effect in previous:effect.pop('searching_player',None)
        if list(current.effects)==previous:shadowed.add(old.key)
    return shadowed


def _conjuncts(condition):
    if not isinstance(condition,Mapping):return None
    if set(condition)=={'all'}:
        if not isinstance(condition['all'],(list,tuple)):return None
        result=[]
        for child in condition['all']:
            values=_conjuncts(child)
            if values is None:return None
            result.extend(values)
        return result
    return [dict(condition)]


def _upkeep_type_count(condition):
    leaves=_conjuncts(condition)
    if leaves is None:return None
    for expected in ({'field':'player','op':'eq','value':'$source.controller'},
            {'field':'step','op':'eq','value':'upkeep'}):
        if leaves.count(expected)!=1:return None
        leaves.remove(expected)
    phase={'field':'phase','op':'eq','value':'beginning'}
    if phase in leaves:leaves.remove(phase)
    if len(leaves)!=1:return None
    leaf=leaves[0]
    if leaf.get('field')=='source_controller_type_count':
        if (set(leaf)!={'field','type','op','value'} or leaf['type'] not in PERMANENT_CARD_TYPES
                or leaf['op']!='gte' or type(leaf['value']) is not int or leaf['value']<1):return None
        return leaf['type'],leaf['value']
    if set(leaf)!={'field','op','value','condition'} or leaf['field']!='fixed_public_state_condition' or leaf['op']!='truthy' or leaf['value'] is not True:return None
    try:spec=FixedPublicStateConditionSpec.from_dict(leaf['condition'])
    except (TypeError,ValueError):return None
    if spec.kind is not FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST or type(spec.amount) is not int or spec.amount<1:return None
    quantity=spec.quantity
    if quantity is None or quantity.query is None or len(quantity.query.types_all)!=1:return None
    card_type=quantity.query.types_all[0]
    if card_type not in PERMANENT_CARD_TYPES:return None
    expected=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST,amount=spec.amount,
        quantity=CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=('battlefield',),types_all=(card_type,))),schema_version=2)
    return (card_type,spec.amount) if spec==expected else None


def _upkeep_life_result(program):
    if len(program.effects)!=1:return None
    effect=dict(program.effects[0]);reason=effect.pop('reason',None)
    if reason is not None and type(reason) is not str:return None
    if (set(effect)!={'op','player','delta'} or effect['op']!='life' or effect['player']!='$controller'
            or type(effect['delta']) is not int or effect['delta']<1):return None
    return effect


def shadowed_reviewed_upkeep_type_count_keys(record,generated_programs,reviewed_programs):
    shadowed=set()
    for current in generated_programs:
        scope=_upkeep_type_count(current.event_condition)
        if (current.event!='step.begin' or current.active_zone!='battlefield' or scope is None
                or current.handlers or current.target_schema is not None or current.cost_schema is not None
                or not {'life.change.effect','trigger.condition.fixed_public_state'}<=set(current.capability_dependencies)):
            continue
        result=_upkeep_life_result(current)
        if result is None:continue
        for old in reviewed_programs:
            if (_same_current_source(record,current,old) and not old.handlers and old.cost_schema is None
                    and scope==_upkeep_type_count(old.event_condition) and result==_upkeep_life_result(old)):
                shadowed.add(old.key)
    return shadowed
