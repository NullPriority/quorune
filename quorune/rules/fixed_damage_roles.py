"""Bind revalidated fixed shares to the canonical allocated damage batch."""
from collections.abc import Mapping

from ..fixed_damage_role_model import (FIXED_DAMAGE_ROLE_FIELD,FIXED_DAMAGE_ROLE_MECHANIC,
    FIXED_DAMAGE_ROLE_CAPABILITY,fixed_damage_role_spec)


def fixed_damage_role_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or FIXED_DAMAGE_ROLE_MECHANIC not in mechanic_ids:return ()
    try:spec=fixed_damage_role_spec(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    if not isinstance(target_schema,Mapping) or set(target_schema)!={'groups'}:return ()
    raw=target_schema['groups']
    if not isinstance(raw,(list,tuple)) or len(raw)!=len(spec.amounts):return ()
    from ..targets import target_plan
    from .damage_capability_shapes import fixed_damage_node_capabilities
    try:
        plan=target_plan(target_schema)
        if len(plan.groups)!=len(raw) or any((g.min_targets,g.max_targets)!=(low,high)
                for g,low,high in zip(plan.groups,spec.minimums,spec.maximums,strict=True)):return ()
        required={FIXED_DAMAGE_ROLE_CAPABILITY,'damage.batch.fixed_set','target.revalidate_resolution'}
        previous=[]
        for index,(group,amount) in enumerate(zip(raw,spec.amounts,strict=True)):
            if not isinstance(group,Mapping) or group.get('id')!='damage_role_'+str(index):return ()
            different=group.get('different_from_groups',[])
            if different not in ([],previous):return ()
            schema={k:v for k,v in group.items() if k not in {'id','different_from_groups'}}
            if spec.minimums[index]==0:
                if (schema.pop('min',None),schema.pop('max',None))!=(spec.minimums[index],spec.maximums[index]):return ()
                schema['count']=1
            owned=fixed_damage_node_capabilities(effects=({'op':'damage','source':'$source','target':'$target.0','amount':amount},),
                target_schema=schema,mechanic_ids={'cr-120-damage','cr-115-targets'})
            if not owned:return ()
            required.update(owned);previous=[*previous,group['id']]
    except (ValueError,TypeError,KeyError):return ()
    return tuple(sorted(required))


def fixed_damage_role_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if FIXED_DAMAGE_ROLE_FIELD in value:
                owned=fixed_damage_role_node_capabilities(effects=(value,),target_schema=program.target_schema,
                    mechanic_ids=(FIXED_DAMAGE_ROLE_MECHANIC,))
                if not owned:raise ValueError('Fixed damage roles require closed target schemas and printed shares')
                required.update(owned);return
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def is_closed_fixed_damage_role_program(program):
    owned=fixed_damage_role_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(owned) and set(owned)<=set(program.capability_dependencies)


def bind_fixed_damage_roles(host,effect,item):
    from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD,AllocatedDamageRecipient
    from .damage_snapshots import recipient_snapshot
    spec=fixed_damage_role_spec(effect)
    groups=item.context.get('target_groups')
    if item.context.get('targets_revalidated') is not True or not isinstance(groups,Mapping) or set(groups)!={'damage_role_'+str(i) for i in range(len(spec.amounts))}:
        raise ValueError('Fixed damage shares require their revalidated target slots')
    shares=[];original=[]
    for index,(amount,low,high) in enumerate(zip(spec.amounts,spec.minimums,spec.maximums,strict=True)):
        refs=groups['damage_role_'+str(index)]
        if (not isinstance(refs,(list,tuple)) or not low<=len(refs)<=high
                or any(type(r) is not str or not r for r in refs) or len(set(refs))!=len(refs)):
            raise ValueError('Fixed damage roles lost their announced cardinality')
        shares.extend([amount]*len(refs));original.extend(refs)
    if len(item.targets)!=len(shares) or any(ref is not None and ref!=old for ref,old in zip(item.targets,original,strict=True)):
        raise ValueError('Fixed damage roles differ from their announced targets')
    recipients={}
    for ref,amount in zip(item.targets,shares,strict=True):
        if ref is None:continue
        current=recipient_snapshot(host,ref,actor=item.controller)
        previous=recipients.get(ref)
        if previous is not None and previous.logical_object_id!=current.logical_object_id:
            raise ValueError('Repeated damage roles disagree about the current incarnation')
        recipients[ref]=AllocatedDamageRecipient(ref,current.logical_object_id,amount+(previous.amount if previous else 0))
    return {'op':'damage_fixed_set','source':'$source','amount':spec.total,
        ANNOUNCED_DAMAGE_EFFECT_FIELD:[r.to_dict() for r in recipients.values()]}
