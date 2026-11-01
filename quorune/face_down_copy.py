"""Status-layer copy snapshots and private-source-free creation identity."""
from collections.abc import Mapping
from copy import deepcopy
from .continuous_effect_model import Layer
from .entry_copy import COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD
from .errors import GameRuleError
from .face_down_characteristics_model import face_down_characteristics_mode
from .morph import MORPH_FACE_DOWN_ANNOTATION

ANONYMOUS_FACE_DOWN_SOURCE='custom-copy:face-down'


def current_face_down_copy_values(host,card):
    if face_down_characteristics_mode(host)!=1 or not card.face_down:return None
    if not isinstance(card.annotations.get(MORPH_FACE_DOWN_ANNOTATION),Mapping):
        raise GameRuleError('Face-down copying requires represented status characteristics')
    values=host._effective_card_data(card,maximum_layer=Layer.COPY)
    fields=('name','mana_cost','mana_value','type_line','oracle_text','executable_oracle_text',
        'power','toughness','colors','keywords','ability_fragments')
    result={field:deepcopy(values[field]) for field in fields if field in values}
    result.update(loyalty=None,defense=None,produced_mana=[],activated_abilities=[])
    result[COPIABLE_ORACLE_FIELD]=ANONYMOUS_FACE_DOWN_SOURCE
    result[COPIABLE_FACE_FIELD]='front'
    return result


def anonymous_face_down_values(host,values):
    return face_down_characteristics_mode(host)==1 and values.get(COPIABLE_ORACLE_FIELD)==ANONYMOUS_FACE_DOWN_SOURCE


def copy_object_identity(host,source,values,ref):
    if anonymous_face_down_values(host,values):
        if type(values.get('name')) is not str:raise ValueError('Copied name must retain its exact characteristic string')
        return values['name'],'custom-copy:'+host._stable_runtime_id('copy-oracle',ref),None
    return (str(values.get('name') or (source.printed_name if source is not None else 'Copy')),
        source.oracle_id if source is not None else 'custom-copy:'+host._stable_runtime_id('copy-oracle',ref),
        source.active_face if source is not None else None)


def token_snapshot_identity(host,source,values):
    if anonymous_face_down_values(host,values):return ANONYMOUS_FACE_DOWN_SOURCE,values['name']
    return source.oracle_id,source.printed_name
