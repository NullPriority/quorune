from __future__ import annotations

"""Two complete fixed target schemas selected by one sealed Kicker decision."""

from collections.abc import Mapping
from copy import deepcopy

KICKED_TARGET_REPLACEMENT_CAPABILITY='resolution.effect.kicker_target_replacement'


def validate_kicked_target_replacement_binding(value):
    if not isinstance(value,Mapping) or set(value)!={'schema_version','normal','kicked'}:
        raise ValueError('Kicked target replacement fields are malformed')
    if type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('Kicked target replacement version is malformed')
    for name in ('normal','kicked'):
        schema=value[name]
        if (not isinstance(schema,Mapping) or type(schema.get('count')) is not int or not 1<=schema['count']<=6
                or set(schema)&{'groups','modes','count_parameter','min','max','minimum','maximum','up_to','id','group','effects'}):
            raise ValueError('Kicked target replacement requires a complete fixed target schema')
    if value['normal']==value['kicked']:raise ValueError('Kicked target replacement must change its target contract')
    return value


def bound_kicked_replacement_schema(value,kicked):
    validate_kicked_target_replacement_binding(value)
    if type(kicked) is not bool:raise ValueError('Kicked target replacement requires a sealed boolean')
    return deepcopy(value['kicked' if kicked else 'normal'])


def _references(value):
    if isinstance(value,str):return {value} if value.startswith('$target') else set()
    if isinstance(value,Mapping):return {r for child in value.values() for r in _references(child)}
    if isinstance(value,(list,tuple)):return {r for child in value for r in _references(child)}
    return set()


def validate_kicked_target_replacement_instruction(effect):
    from .resolution_conditions import KickedCastCondition,cast_fact_result_is_fixed
    if (not isinstance(effect,Mapping) or set(effect)!={'op','schema_version','player','condition','cast_fact',
            'effects','mechanic_ids','otherwise_effects','otherwise_mechanic_ids','target_binding'}
            or effect.get('op')!='apply_if_public_condition' or type(effect.get('schema_version')) is not int
            or effect['schema_version']!=11 or effect['condition']!=KickedCastCondition().to_dict()
            or not(type(effect['cast_fact']) is bool or effect['cast_fact']=='$context.kicked')
            or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Kicked target replacement identity is malformed')
    binding=validate_kicked_target_replacement_binding(effect['target_binding'])
    for key,ids,schema in [('effects','mechanic_ids',binding['kicked']),('otherwise_effects','otherwise_mechanic_ids',binding['normal'])]:
        values=effect[key];mechanics=effect[ids]
        if (not isinstance(mechanics,(list,tuple)) or not mechanics
                or any(type(v) is not str or not v for v in mechanics) or len(mechanics)!=len(set(mechanics))
                or not isinstance(values,(list,tuple)) or not 1<=len(values)<=4
                or any(not isinstance(v,Mapping) or v.get('op')=='apply_if_public_condition' for v in values)
                or not _references(values) or not _references(values)<={'$target.0','$targets'}
                or schema['count']>1 and _references(values)!={'$targets'}
                or not cast_fact_result_is_fixed(values,mechanics,allow_targeted_damage_source=True)):
            raise ValueError('Kicked replacement branch has an unrepresented target or result')
    return KickedCastCondition(),tuple(effect['effects'])
