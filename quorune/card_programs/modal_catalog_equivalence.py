from __future__ import annotations

"""Current typed equivalents of source-pinned catalog-only modal views."""

from collections.abc import Mapping
from copy import deepcopy
from ..replacement.immutable import thaw_value
from ..util import stable_json


def _mode_values(schema):
    if not isinstance(schema,Mapping) or set(schema)-{'mode_count','min_modes','max_modes','modes'}:return None
    if any(type(schema.get(key,1)) is not int or schema.get(key,1)!=1 for key in ('mode_count','min_modes','max_modes')):return None
    modes=schema.get('modes')
    if not isinstance(modes,Mapping) or not 2<=len(modes)<=5:return None
    result=[]
    for index,raw in enumerate(modes.values(),1):
        if not isinstance(raw,Mapping):return None
        value=deepcopy(dict(raw));effects=value.pop('effects',None);value.pop('mechanics',None)
        if not isinstance(effects,(list,tuple)) or not effects or any(not isinstance(e,Mapping) for e in effects):return None
        if 'groups' in value:
            groups=value.pop('groups')
            if value or not isinstance(groups,(list,tuple)) or len(groups)>1:return None
            if groups:
                value=dict(groups[0]);identifier=value.pop('id',None)
                if identifier!=f'mode_{index}_target_1':return None
            else:value=None
        if value is not None:
            if type(value.get('count')) is not int or value['count']!=1:return None
            for kind in ('artifact','creature','enchantment','land','planeswalker','permanent'):
                if kind not in value:continue
                if value.pop(kind) is not True or 'types_any' in value:return None
                if kind!='permanent':value['types_any']=[kind]
            from ..targets import target_plan
            try:
                if len(target_plan(value).groups)!=1:return None
            except (ValueError,TypeError):return None
        normalized=[]
        for raw_effect in effects:
            effect=dict(raw_effect)
            if effect.get('op')=='draw' and 'private' not in effect:effect['private']=True
            normalized.append(effect)
        result.append((value,normalized))
    return result


def shadowed_reviewed_modal_catalog_keys(record,generated_programs,reviewed_programs):
    from ..compiler.modal_program_closure import is_closed_fixed_modal_program
    from ..compiler.activated_ability_catalog import activated_abilities_from_program
    from ..compiler.activated_costs import activated_ability_cost
    reviewed={p.key:p for p in reviewed_programs};shadowed=set()
    for current in generated_programs:
        old=reviewed.get(current.key)
        if (old is None or current.oracle_id!=record.oracle_id or old.oracle_id!=record.oracle_id
                or current.trust_level!='trusted' or old.trust_level!='trusted'
                or str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
                or not current.capability_closure or current.capability_closure.get('trusted') is not True
                or not is_closed_fixed_modal_program(current)
                or current.event!='activate' or current.event_condition is not None or old.event_condition is not None
                or any(getattr(current,key)!=getattr(old,key) for key in ('ability_id','version','event','active_zone','destination','requires_arbiter'))
                or current.effects or old.effects or old.cost_schema is not None
                or any(not current.provenance.get(key) or current.provenance[key]!=old.provenance.get(key)
                    for key in ('source_oracle_hash','source_rulings_hash'))):continue
        if len(current.handlers)!=1 or len(old.handlers)!=1:continue
        a=deepcopy(thaw_value(current.handlers[0]));b=deepcopy(thaw_value(old.handlers[0]))
        if (set(a)!={'handler_id','schema_version','event','ability'} or set(b)!=set(a)
                or a['handler_id']!='activation.catalog.pinned.v1' or b['handler_id']!=a['handler_id']
                or type(a['schema_version']) is not int or type(b['schema_version']) is not int
                or a['schema_version']!=1 or b['schema_version']!=1 or a['event']!=b['event'] or a['event']!='activate'
                or not isinstance(a['ability'],dict) or not isinstance(b['ability'],dict)):continue
        first=a['ability'];second=b['ability']
        if first.pop('target_schema',None)!=thaw_value(current.target_schema) or second.pop('target_schema',None)!=thaw_value(old.target_schema):continue
        if stable_json(first)!=stable_json(second):continue
        try:abilities=activated_abilities_from_program(current)
        except (ValueError,TypeError):continue
        if len(abilities)!=1 or stable_json(current.cost_schema)!=stable_json(activated_ability_cost(abilities[0])):continue
        new_modes=_mode_values(thaw_value(current.target_schema));old_modes=_mode_values(thaw_value(old.target_schema))
        if new_modes is None or old_modes is None or stable_json(new_modes)!=stable_json(old_modes):continue
        shadowed.add(old.key)
    return shadowed
