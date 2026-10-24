from __future__ import annotations

"""Closed operational equivalence for current and reviewed trigger views."""

from copy import deepcopy
from dataclasses import replace
import hashlib,json
from ..targets import target_plan


def _condition(value,event):
    if value is None:return None
    value=deepcopy(value)
    if (event=='creature.dies' and isinstance(value,dict)
            and value=={'field':'previous_controller','op':'eq','value':'$source.controller'}):
        return {**value,'field':'controller'}
    if not isinstance(value,dict) or set(value)!={'all'} or not isinstance(value['all'],list):return value
    leaves=[]
    for leaf in value['all']:
        if not isinstance(leaf,dict) or set(leaf)!={'field','op','value'}:return value
        field,op,arg=leaf['field'],leaf['op'],leaf['value']
        if field in {'controller','previous_controller','card'}:
            if op not in {'eq','ne'} or not isinstance(arg,str):return value
        elif field=='types':
            if op not in {'contains_any','contains_all'} or not isinstance(arg,list) or not all(isinstance(x,str) for x in arg):return value
        else:return value
        leaves.append(_condition(leaf,event))
    return {'all':sorted(leaves,key=lambda x:json.dumps(x,sort_keys=True))}


def _effects(program):
    from .reviewed_overlay import _canonical_effects
    effects=deepcopy(_canonical_effects(program))
    for effect in effects:
        if effect.get('op')=='destroy' and isinstance(effect.get('reason'),str):effect.pop('reason')
        if effect.get('op')=='create_token' and isinstance(effect.get('characteristics'),dict):
            chars=effect['characteristics'];line=chars.get('type_line')
            if isinstance(line,str) and line.startswith('Token '):
                from ..characteristic_evaluation import type_parts
                stripped=line.removeprefix('Token ')
                if type_parts(line)==type_parts(stripped):chars['type_line']=stripped
    return effects


def _targets_equal(current,old):
    if current.target_schema==old.target_schema:return True
    # Only one positional destroy result uses this schema normalization.
    effects=_effects(current)
    if (effects!=_effects(old) or len(effects)!=1 or effects[0].get('op')!='destroy'
            or effects[0].get('card')!='$target.0'):return False
    if current.target_schema is None or old.target_schema is None:return False
    try:a,b=(target_plan(p.target_schema) for p in (current,old))
    except (TypeError,ValueError,KeyError):return False
    if (a.modes or b.modes or a.globally_distinct or b.globally_distinct
            or a.same_player_groups or b.same_player_groups or len(a.groups)!=1 or len(b.groups)!=1):return False
    if any(g.min_targets!=1 or g.max_targets!=1 for g in (*a.groups,*b.groups)):return False
    return replace(a.groups[0],group_id='target')==replace(b.groups[0],group_id='target')


def shadowed_reviewed_trigger_view_keys(record,generated_programs,reviewed_programs,*,capability_registry=None,capability_profile='traditional'):
    if capability_registry is None or record.layout!='normal' or record.faces:return set()
    from .binding import bind_semantic_program_runtime
    generated=tuple(generated_programs);reviewed=tuple(reviewed_programs);shadowed=set()
    oracle_hash=hashlib.sha256(record.oracle_text.encode('utf-8')).hexdigest()
    fields=('semantic_schema_version','active_zone','event','destination','cost_schema','handlers','trigger_limit','requires_arbiter')
    for old in reviewed:
        if (old.oracle_id!=record.oracle_id or old.trust_level!='trusted' or old.requires_arbiter
                or not old.ability_id.startswith('trigger:')
                or str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
                or old.provenance.get('source_oracle_hash')!=oracle_hash
                or not old.provenance.get('source_rulings_hash')):continue
        matches=[]
        for current in generated:
            if (current.oracle_id!=record.oracle_id or current.trust_level!='trusted'
                    or not current.ability_id.startswith('trigger:')
                    or not str(current.provenance.get('authored_by','')).startswith('oracle-ir-v')
                    or current.provenance.get('source_oracle_hash')!=oracle_hash
                    or current.provenance.get('source_rulings_hash')!=old.provenance['source_rulings_hash']
                    or any(getattr(current,key)!=getattr(old,key) for key in fields)
                    or _condition(current.event_condition,current.event)!=_condition(old.event_condition,old.event)
                    or _effects(current)!=_effects(old) or not _targets_equal(current,old)):continue
            if bind_semantic_program_runtime(current,capability_registry=capability_registry,profile=capability_profile)['strict']:
                matches.append(current)
        if len(matches)==1:shadowed.add(old.key)
    return shadowed
