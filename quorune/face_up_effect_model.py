"""Closed face-up instructions and public face-transition facts."""
from collections.abc import Mapping

FACE_UP_EFFECT_OPERATION='turn_face_up'
FACE_UP_EFFECT_CAPABILITY='permanent.face_up.effect'
FACE_UP_EFFECT_MECHANIC='fixed-target-face-up'
FACE_UP_EFFECT_RULES=('101.2','613.7f','701.20a','701.40g','708.8','730.2f','730.2g')
TURN_FACE_UP_EVENT = "permanent.turned_face_up"
TURN_FACE_UP_ACTOR_FIELD = "turn_face_up_actor"
TURN_FACE_UP_ACTOR_CAPABILITY = "trigger.event.turn_face_up_actor"
FACE_DOWN_ENTRY_FIELD = "entering_face_down"
FACE_DOWN_ENTRY_CAPABILITY = "trigger.event.face_down_entry"


def face_up_instruction(card='$target.0'):
    return {'op':FACE_UP_EFFECT_OPERATION,'schema_version':1,'card':card}


def face_up_target_schema(relation):
    if relation not in {'any','you','opponent'}:raise ValueError('Face-up target controller relation is unsupported')
    return {'zones':['battlefield'],'types_any':['creature'],'count':1,'face_down':True,
        **({'controller_relation':relation} if relation!='any' else {})}


def validate_face_up_instruction(effect,*,allow_runtime_source=False):
    allowed={'op','schema_version','card','reason'}|({'_runtime_source'} if allow_runtime_source else set())
    if (not isinstance(effect,Mapping) or set(effect)-allowed or not {'op','schema_version','card'}.issubset(effect)
            or effect['op']!=FACE_UP_EFFECT_OPERATION or type(effect['schema_version']) is not int
            or effect['schema_version']!=1 or type(effect['card']) is not str or not effect['card']
            or effect['card']!=effect['card'].strip()
            or 'reason' in effect and (type(effect['reason']) is not str or not effect['reason'])):
        raise ValueError('Face-up instruction has a closed versioned single-reference shape')


def face_up_node_capabilities(*,effects,target_schema,mechanics):
    if set(mechanics)!={FACE_UP_EFFECT_MECHANIC,'cr-115-targets'} or tuple(effects)!=(face_up_instruction(),):return ()
    try:validate_face_up_instruction(effects[0])
    except ValueError:return ()
    if not isinstance(target_schema,Mapping) or type(target_schema.get('count')) is not int or target_schema.get('face_down') is not True:return ()
    if not any(target_schema==face_up_target_schema(relation) for relation in ('any','you','opponent')):return ()
    return FACE_UP_EFFECT_CAPABILITY,'target.revalidate_resolution'
