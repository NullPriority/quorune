from __future__ import annotations

"""Current source-pinned equivalents of reviewed optional Kicker target views."""

from copy import deepcopy
from ..replacement.immutable import thaw_value


def _schema(value):
    if not isinstance(value,dict):return None
    result=deepcopy(value)
    if result.pop('land',None) is False:
        if result.get('types_none') not in (None,['land']):return None
        result['types_none']=['land']
    if type(result.get('count')) is not int or not 1<=result['count']<=6:return None
    return result


def _effects(values):
    result=[]
    for raw in values:
        value=dict(raw)
        if value.get('op')=='exile':value['op']='exile_permanent'
        result.append(value)
    return result


def shadowed_reviewed_kicker_target_keys(record,generated_programs,reviewed_programs):
    from ..kicked_target_replacement_model import KICKED_TARGET_REPLACEMENT_CAPABILITY
    from ..rules.kicked_target_replacement_shapes import kicked_target_replacement_capabilities
    from ..kicker import KICKER_COST_HANDLER_ID,FixedManaKickerSpec
    reviewed={p.key:p for p in reviewed_programs};result=set()
    kicker=[]
    for p in generated_programs:
        for handler in p.handlers:
            if handler.get('handler_id')==KICKER_COST_HANDLER_ID:
                try:kicker.append(FixedManaKickerSpec.from_dict(handler['kicker']))
                except (TypeError,ValueError):pass
    if len(kicker)!=1:return result
    spec=kicker[0]
    for current in generated_programs:
        old=reviewed.get(current.key)
        if (old is None or current.oracle_id!=old.oracle_id or current.oracle_id!=record.oracle_id
                or current.trust_level!=old.trust_level or current.trust_level!='trusted'
                or not current.capability_closure or current.capability_closure.get('trusted') is not True
                or str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
                or any(not current.provenance.get(key) or current.provenance[key]!=old.provenance.get(key)
                    for key in ('source_oracle_hash','source_rulings_hash'))
                or any(getattr(current,key)!=getattr(old,key) for key in ('event','active_zone','destination','requires_arbiter','version'))
                or current.key!=old.key
                or current.event!='resolve' or current.active_zone!='stack' or current.event_condition is not None or old.event_condition is not None
                or current.handlers or old.handlers or current.cost_schema is not None
                or KICKED_TARGET_REPLACEMENT_CAPABILITY not in current.capability_dependencies):continue
        requirements=kicked_target_replacement_capabilities(effects=current.effects,target_schema=current.target_schema,mechanic_ids=current.coverage)
        if not requirements or not set(requirements)<=set(current.capability_dependencies):continue
        value=thaw_value(old.cost_schema)
        if not isinstance(value,dict) or set(value)!={'optional_costs'} or len(value['optional_costs'])!=1:continue
        option=value['optional_costs'][0]
        if (set(option)!={'id','kind','label','requirements','target_schema','effects'}
                or option['id']!='kicked' or option['kind']!='kicker' or option['label']!='Kicker '+spec.cost_text):continue
        if (not isinstance(option['requirements'],dict) or set(option['requirements'])-set(spec.mana_cost)
                or any(type(v) is not int or v<0 for v in option['requirements'].values())):continue
        mana={key:0 for key in spec.mana_cost};mana.update(option['requirements'])
        if mana!=dict(spec.mana_cost):continue
        wrapper=current.effects[0];binding=wrapper['target_binding']
        schemas=tuple(_schema(value) for value in (thaw_value(old.target_schema),thaw_value(binding['normal']),
            option['target_schema'],thaw_value(binding['kicked'])))
        if any(schema is None for schema in schemas):continue
        if (schemas[0]!=schemas[1] or schemas[2]!=schemas[3]
                or _effects(old.effects)!=_effects(wrapper['otherwise_effects'])
                or _effects(option['effects'])!=_effects(wrapper['effects'])):continue
        result.add(old.key)
    return result
