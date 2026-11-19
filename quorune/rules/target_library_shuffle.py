"""One explicit target binding over the existing canonical shuffle owner."""
from collections.abc import Mapping
from ..library_order_model import SHUFFLE_OPERATION, SHUFFLE_CAPABILITY, SHUFFLE_MECHANIC

TARGET_SHUFFLE_FIELD='target_library_shuffle_version'
TARGET_SHUFFLE_CAPABILITY='library.shuffle.fixed_target_player'
TARGET_SHUFFLE_MECHANIC='fixed-target-player-library-shuffle'


def target_shuffle_schema():
    return {'zones':['player'],'categories':['player'],'player_relation':'any','count':1}


def validate_target_shuffle_effect(value, *, resolved=False):
    if (not isinstance(value,Mapping) or set(value)!={'op','player',TARGET_SHUFFLE_FIELD}
            or value.get('op')!=SHUFFLE_OPERATION or type(value.get(TARGET_SHUFFLE_FIELD)) is not int
            or value[TARGET_SHUFFLE_FIELD]!=1 or type(value.get('player')) is not str):
        raise ValueError('Target shuffle requires a complete version-one player binding')
    player=value['player']
    if (not player or player.startswith('$')) if resolved else player!='$target.0':
        raise ValueError('Target shuffle requires its represented player reference')
    return player


def target_shuffle_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (len(effects)!=1 or TARGET_SHUFFLE_MECHANIC not in mechanic_ids
            or 'cr-115-targets' not in mechanic_ids or not isinstance(target_schema,Mapping)
            or dict(target_schema)!=target_shuffle_schema() or type(target_schema.get('count')) is not int):return ()
    try:validate_target_shuffle_effect(effects[0])
    except (TypeError,ValueError):return ()
    return (TARGET_SHUFFLE_CAPABILITY,SHUFFLE_CAPABILITY,'target.revalidate_resolution')


def target_shuffle_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if TARGET_SHUFFLE_FIELD in value:
                validate_target_shuffle_effect(value)
                schema=program.target_schema
                if (not isinstance(schema,Mapping) or dict(schema)!=target_shuffle_schema()
                        or type(schema.get('count')) is not int):
                    raise ValueError('Target shuffle requires one complete public player target')
                required.update((TARGET_SHUFFLE_CAPABILITY,SHUFFLE_CAPABILITY,'target.revalidate_resolution'))
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def shuffle_covered_mechanics(supplied):
    return {mechanic for capability,mechanic in (
        (SHUFFLE_CAPABILITY,SHUFFLE_MECHANIC),
        (TARGET_SHUFFLE_CAPABILITY,TARGET_SHUFFLE_MECHANIC),
    ) if capability in supplied}
