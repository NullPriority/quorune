from __future__ import annotations

"""Closed cast-time target roles for one Kicker-dependent fixed result."""

from collections.abc import Mapping
from copy import deepcopy

KICKED_TARGET_CAPABILITY='resolution.effect.kicker_conditional_targets'
KICKED_TARGET_SCHEMA_VERSION=10
KICKED_TARGET_FIELD='kicked_target_binding'


def validate_kicked_target_binding(value):
    if not isinstance(value,Mapping) or set(value)!={'schema_version','groups','conditional_group'}:
        raise ValueError('Kicked target binding fields are malformed')
    if type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('Kicked target binding version is malformed')
    groups=value['groups']
    if not isinstance(groups,(list,tuple)) or not 1<=len(groups)<=2 or value['conditional_group']!='target_1':
        raise ValueError('Kicked targets require one conditional role and at most one prefix role')
    expected_ids=['target_1'] if len(groups)==1 else ['target_0','target_1']
    for group,identity in zip(groups,expected_ids,strict=True):
        if (not isinstance(group,Mapping) or set(group)!={'id','schema','different_from_groups'}
                or group['id']!=identity or not isinstance(group['schema'],Mapping)
                or type(group['schema'].get('count')) is not int or group['schema']['count']!=1
                or set(group['schema'])&{'groups','modes','count_parameter','min','max','minimum','maximum',
                    'up_to','id','group','globally_distinct','same_player_groups','different_from_groups','effects'}
                or not isinstance(group['different_from_groups'],(list,tuple))
                or tuple(group['different_from_groups']) not in ((),('target_0',))):
            raise ValueError('Kicked target group is malformed')
        if group['different_from_groups'] and (identity!='target_1' or len(groups)!=2):
            raise ValueError('Another-target relation requires a preceding role')
    return value


def bound_kicked_target_schema(binding,kicked):
    validate_kicked_target_binding(binding)
    if type(kicked) is not bool:raise ValueError('Kicked target count requires a sealed boolean')
    groups=[]
    for entry in binding['groups']:
        count=int(entry['id']!='target_1' or kicked)
        groups.append({**{k:deepcopy(v) for k,v in entry['schema'].items() if k!='count'},
            'id':entry['id'],'min':count,'max':count,'different_from_groups':list(entry['different_from_groups'])})
    return {'groups':groups}


def scope_target_role(value,role,*,restore=False):
    if isinstance(value,Mapping):return {k:scope_target_role(v,role,restore=restore) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [scope_target_role(v,role,restore=restore) for v in value]
    reference='$target_group.'+role+'.0'
    if restore:return '$target.0' if value==reference else value
    return reference if value=='$target.0' else value


def target_roles(value):
    if isinstance(value,str):return {value} if value.startswith('$target') else set()
    if isinstance(value,Mapping):return {r for child in value.values() for r in target_roles(child)}
    if isinstance(value,(list,tuple)):return {r for child in value for r in target_roles(child)}
    return set()


def validate_kicked_target_instruction(effect):
    from .resolution_conditions import KickedCastCondition,cast_fact_result_is_fixed
    if (not isinstance(effect,Mapping) or set(effect)!={'op','schema_version','player','condition','cast_fact',
            'effects','mechanic_ids','prefix_mechanic_ids','target_binding'}
            or effect.get('op')!='apply_if_public_condition' or type(effect.get('schema_version')) is not int
            or effect['schema_version']!=10 or effect['condition']!=KickedCastCondition().to_dict()
            or not(type(effect['cast_fact']) is bool or effect['cast_fact']=='$context.kicked')
            or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Kicked targeted instruction identity is malformed')
    validate_kicked_target_binding(effect['target_binding'])
    for key in ('mechanic_ids','prefix_mechanic_ids'):
        values=effect[key]
        if not isinstance(values,(list,tuple)) or any(type(v) is not str or not v for v in values) or len(values)!=len(set(values)):
            raise ValueError('Kicked target mechanics are malformed')
    children=effect['effects']
    if (not isinstance(children,(list,tuple)) or not 1<=len(children)<=4 or not effect['mechanic_ids']
            or any(not isinstance(v,Mapping) or v.get('op')=='apply_if_public_condition' for v in children)
            or target_roles(children)!={'$target_group.target_1.0'}
            or not cast_fact_result_is_fixed(children,effect['mechanic_ids'],allow_targeted_damage_source=True)):
        raise ValueError('Kicked target children require one independently represented fixed role')
    return KickedCastCondition(),tuple(children)
