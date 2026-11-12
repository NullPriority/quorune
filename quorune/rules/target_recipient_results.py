"""Closed projection of one target-derived recipient onto an existing result."""
from collections.abc import Mapping
from copy import deepcopy

from ..target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_KIND, TARGET_CONTROLLER_INFORMATION_CAPABILITY, TARGET_CONTROLLER_INFORMATION_MECHANIC,
    TARGET_OWNER_INFORMATION_KIND, TARGET_OWNER_INFORMATION_CAPABILITY, TARGET_OWNER_INFORMATION_MECHANIC,
    controller_reference_index, owner_reference_index,
)

_KINDS = {TARGET_CONTROLLER_INFORMATION_KIND,TARGET_OWNER_INFORMATION_KIND}
_PLAYER_SCHEMA = {'zones':['player'],'categories':['player'],'count':1,'player_relation':'any'}
_COUNTER_SCHEMA = {'zones':['player'],'categories':['player'],'count':1}
_PREFIX_OPS = {'destroy','exile_permanent','bounce','move','exile_public_graveyard_card'}


def contains_target_recipient_information(effect):
    if not isinstance(effect,Mapping):return False
    values = [effect.get('player'),effect.get('target'),effect.get('controller')]
    players = effect.get('players')
    if isinstance(players,(list,tuple)):values.extend(players)
    return any(isinstance(value,Mapping) and value.get('kind') in _KINDS for value in values)


def target_recipient_prefix_is_closed(effect,schema,relation):
    if (not isinstance(effect,Mapping) or effect.get('op') not in _PREFIX_OPS
            or effect.get('card') != '$target.0' or not isinstance(schema,Mapping)
            or type(schema.get('count')) is not int or schema['count'] != 1 or schema.get('up_to')):return False
    if effect['op']=='move' and effect.get('destination')!='library':return False
    if relation=='controller':return schema.get('zones')==['battlefield'] and schema.get('categories')==['permanent']
    if relation!='owner':return False
    return ((schema.get('zones')==['battlefield'] and schema.get('categories')==['permanent'])
        or effect['op']=='exile_public_graveyard_card' and schema.get('zones')==['graveyard'] and schema.get('categories')==['card'])


def _fixed_result_shape(effect,schema):
    if not isinstance(effect,Mapping):return False
    op=effect.get('op')
    if op in {'draw','life','lose_life','create_token'}:
        field={'draw':'count','life':'delta','lose_life':'amount','create_token':'quantity'}[op]
        recipient='controller' if op=='create_token' else 'player'
        return schema is None and effect.get(recipient)=='$controller' and type(effect.get(field)) is int
    if op=='choose_cards_apnap':
        return (schema==_PLAYER_SCHEMA and effect.get('players')==['$target.0']
            and effect.get('target')=='$target.0' and type(effect.get('count')) is int)
    if op=='place_player_counters':
        return schema==_COUNTER_SCHEMA and effect.get('target')=='$target.0' and type(effect.get('amount')) is int
    return False


def bind_fixed_recipient_result(effect,schema,recipient):
    if not _fixed_result_shape(effect,schema):return None
    bound=deepcopy(dict(effect))
    if bound['op']=='choose_cards_apnap':
        bound['players']=[dict(recipient)];bound.pop('target')
    else:
        field='controller' if bound['op']=='create_token' else 'target' if bound['op']=='place_player_counters' else 'player'
        bound[field]=dict(recipient)
    return bound


def _restored_result(result):
    if not isinstance(result,Mapping):return None
    restored=deepcopy(dict(result));op=result.get('op')
    if op=='choose_cards_apnap':
        players=result.get('players')
        if not isinstance(players,(list,tuple)) or len(players)!=1 or 'target' in result:return None
        reference=players[0];restored['players']=['$target.0'];restored['target']='$target.0';schema=_PLAYER_SCHEMA
    elif op=='place_player_counters':
        reference=result.get('target');restored['target']='$target.0';schema=_COUNTER_SCHEMA
    else:
        field='controller' if op=='create_token' else 'player'
        reference=result.get(field);restored[field]='$controller';schema=None
    if not _fixed_result_shape(restored,schema) or not isinstance(reference,Mapping):return None
    kind=reference.get('kind')
    try:
        index=(controller_reference_index(reference) if kind==TARGET_CONTROLLER_INFORMATION_KIND else
            owner_reference_index(reference) if kind==TARGET_OWNER_INFORMATION_KIND else -1)
    except ValueError:return None
    if index!=0:return None
    relation='controller' if kind==TARGET_CONTROLLER_INFORMATION_KIND else 'owner'
    return restored,schema,relation


def target_recipient_result_node_capabilities(*,effects,target_schema,mechanic_ids):
    from ..compiler.target_recipient_results import TARGET_RECIPIENT_RESULT_CAPABILITY,TARGET_RECIPIENT_RESULT_MECHANIC
    from ..compiler.bound_effect_programs import BOUND_EFFECT_PROGRAM_CAPABILITY,BOUND_EFFECT_PROGRAM_MECHANIC
    from ..compiler.closed_effect_programs import CLOSED_EFFECT_PROGRAM_MECHANIC
    from .fixed_effect_clause_shapes import closed_effect_component_capabilities
    mechanics=set(mechanic_ids)
    if (len(effects)!=2 or not {TARGET_RECIPIENT_RESULT_MECHANIC,BOUND_EFFECT_PROGRAM_MECHANIC,CLOSED_EFFECT_PROGRAM_MECHANIC}<=mechanics):return ()
    projected=_restored_result(effects[1])
    if projected is None:return ()
    restored,schema,relation=projected
    information=(TARGET_CONTROLLER_INFORMATION_CAPABILITY,TARGET_CONTROLLER_INFORMATION_MECHANIC) if relation=='controller' else (
        TARGET_OWNER_INFORMATION_CAPABILITY,TARGET_OWNER_INFORMATION_MECHANIC)
    if information[1] not in mechanics or not target_recipient_prefix_is_closed(effects[0],target_schema,relation):return ()
    prefix=closed_effect_component_capabilities(effects[:1],target_schema=target_schema,mechanics=mechanics)
    result=closed_effect_component_capabilities((restored,),target_schema=schema,mechanics=mechanics)
    if not prefix or not result:return ()
    return tuple(sorted({TARGET_RECIPIENT_RESULT_CAPABILITY,BOUND_EFFECT_PROGRAM_CAPABILITY,
        'resolution.effect_program.closed_components',information[0],*prefix,*result}))


def target_recipient_result_runtime_dependencies(program):
    """A new producer keeps its recipient contract even if annotations are removed."""
    from ..compiler.target_recipient_results import TARGET_RECIPIENT_RESULT_TEMPLATE
    if getattr(program,'provenance',{}).get('template_id')!=TARGET_RECIPIENT_RESULT_TEMPLATE:return ()
    from ..compiler.public_query_effect_amounts import public_query_amount_shape_context
    projected=public_query_amount_shape_context(program.effects,set(program.coverage),target_schema=program.target_schema)
    if projected is None:raise ValueError('Target-recipient production lost its closed amount recipe')
    effects,mechanics=projected
    required=target_recipient_result_node_capabilities(effects=effects,target_schema=program.target_schema,mechanic_ids=mechanics)
    if not required:raise ValueError('Target-recipient production lost its complete target and recipient recipe')
    return required
