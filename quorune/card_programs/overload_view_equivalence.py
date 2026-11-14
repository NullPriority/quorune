"""Retire an unbound compatibility view only for its verified typed pair."""
from collections.abc import Mapping
from dataclasses import replace
import hashlib

from ..overload_model import FixedManaOverloadSpec,overload_body_fingerprint,OVERLOAD_NORMAL,OVERLOAD_PAID
from ..targets import target_plan


def _canonical_single_target(schema):
    plan=target_plan(schema)
    if len(plan.groups)!=1:return None
    group=plan.groups[0];all_types=set(group.types_all);none_types=set(group.types_none)
    for field in ('artifact','creature','land','enchantment'):
        value=getattr(group,field)
        if value is not None:
            (all_types if value else none_types).add(field)
    any_types=group.types_any
    if len(any_types)==1:all_types.update(any_types);any_types=()
    if group.permanent is not None and (group.permanent is not True or group.categories!=('permanent',)):return None
    return replace(group,artifact=None,creature=None,land=None,enchantment=None,permanent=None,
        types_all=tuple(sorted(all_types)),types_none=tuple(sorted(none_types)),types_any=any_types)


def shadowed_reviewed_overload_keys(record,generated,reviewed,*,capability_registry,capability_profile):
    if capability_registry is None:return set()
    from .binding import bind_semantic_program_runtime
    values=tuple(generated);shadowed=set()
    oracle_hash=hashlib.sha256(record.oracle_text.encode('utf-8')).hexdigest()
    def strict(p):return bind_semantic_program_runtime(p,capability_registry=capability_registry,profile=capability_profile)['strict']
    for normal in values:
        if (normal.oracle_id!=record.oracle_id or OVERLOAD_NORMAL not in normal.coverage
                or not isinstance(normal.cost_schema,Mapping) or set(normal.cost_schema)!={'overload'}):continue
        try:spec=FixedManaOverloadSpec.from_dict(normal.cost_schema['overload'])
        except (TypeError,ValueError):continue
        paid=next((p for p in values if p.ability_id==normal.ability_id+':overload' and OVERLOAD_PAID in p.coverage),None)
        if (paid is None or paid.oracle_id!=normal.oracle_id or paid.cost_schema!=normal.cost_schema
                or normal.trust_level!='trusted' or paid.trust_level!='trusted'
                or overload_body_fingerprint(normal.effects,normal.target_schema)!=spec.normal_fingerprint
                or overload_body_fingerprint(paid.effects,paid.target_schema)!=spec.overloaded_fingerprint
                or not strict(normal) or not strict(paid)):continue
        if (normal.provenance.get('source_oracle_hash')!=oracle_hash
                or not str(normal.provenance.get('authored_by','')).startswith('oracle-ir-v')
                or normal.provenance.get('authored_by')!=paid.provenance.get('authored_by')):continue
        if any(not normal.provenance.get(field) or normal.provenance[field]!=paid.provenance.get(field)
                for field in ('source_oracle_hash','source_rulings_hash')):continue
        for old in reviewed:
            if (old.key!=normal.key or old.oracle_id!=record.oracle_id or old.trust_level!='trusted' or old.requires_arbiter
                    or old.active_zone!=normal.active_zone or old.event!=normal.event or old.destination!=normal.destination
                    or old.effects!=normal.effects or old.handlers!=normal.handlers or old.event_condition!=normal.event_condition
                    or strict(old) or not isinstance(old.cost_schema,Mapping) or set(old.cost_schema)!={'alternate_costs'}):continue
            if any(old.provenance.get(field) and old.provenance[field]!=normal.provenance.get(field)
                    for field in ('source_oracle_hash','source_rulings_hash')):continue
            prices=old.cost_schema['alternate_costs']
            if not isinstance(prices,(list,tuple)) or len(prices)!=1:continue
            price=prices[0]
            if (not isinstance(price,Mapping) or set(price)!={'id','kind','label','requirements','target_schema','effects'}
                    or price['id']!='overload' or price['kind']!='overload' or type(price['label']) is not str
                    or price['effects']!=paid.effects or price['target_schema']!={'groups':[]}
                    or not isinstance(price['requirements'],Mapping) or set(price['requirements'])-set(spec.requirements)
                    or any(type(n) is not int or n<0 for n in price['requirements'].values())
                    or {k:price['requirements'].get(k,0) for k in spec.requirements}!=dict(spec.requirements)):continue
            try:
                previous=_canonical_single_target(old.target_schema);current=_canonical_single_target(normal.target_schema)
                equal=previous is not None and current is not None and previous==current
            except (ValueError,TypeError,KeyError):continue
            if equal:shadowed.add(old.key)
    return shadowed
