"""Read-only transport of physical Oracle faces for token-copy creation."""
from collections import ChainMap
from copy import deepcopy
from dataclasses import replace
from .characteristic_read_frames import CharacteristicReadHost,CharacteristicStateView
from .defined_token_faces import (
    CopiableTokenFace,DefinedTransformingToken,DEFINED_TOKEN_FACES_FIELD,
    DEFINED_TOKEN_ACTIVE_FACE_FIELD,DEFINED_TOKEN_IDENTITY_FIELD,single_face_copy_values,
)
from .token_copy_value_model import token_copy_value_mode,token_copy_value_annotations
from .face_down_copy import anonymous_face_down_values



def token_creation_copy_faces(host,source,values):
    if source.permanent_composition is not None:
        return single_face_copy_values(values)
    if (token_copy_value_mode(host)!=1 or source.is_token or source.object_kind!='card' or source.face_down):
        return values
    record=host.card_db.by_oracle_id(source.oracle_id)
    if record is None or record.layout not in {'transform','modal_dfc'}:return values
    if len(record.faces)!=2:raise ValueError('Token copy requires both pinned physical faces')
    names=tuple(str(face.get('name') or '') for face in record.faces)
    if not all(names) or len(set(names))!=2 or source.active_face not in {None,*names}:
        raise ValueError('Token-copy physical orientation changed')
    faces=[]
    for name in names:
        view=replace(source,active_face=name,annotations=deepcopy(source.annotations))
        reader=CharacteristicReadHost(host,CharacteristicStateView(host.state,
            ChainMap({view.object_id:view},host.state.cards),host.state.players))
        copied=single_face_copy_values(reader._copyable_characteristics(view))
        faces.append(CopiableTokenFace(name,copied))
    result=dict(values)
    result[DEFINED_TOKEN_FACES_FIELD]=DefinedTransformingToken(*faces,schema_version=2).to_dict()
    result[DEFINED_TOKEN_ACTIVE_FACE_FIELD]=source.active_face or names[0]
    return result


def spell_copy_characteristics(host,source):
    """Capture physical spell faces now, including a copy of an earlier copy."""
    mode=token_copy_value_mode(host)
    if (mode==1 and source.object_kind=='spell_copy'
            and DEFINED_TOKEN_FACES_FIELD in source.annotations.get('copy_overrides',{})
            and DEFINED_TOKEN_IDENTITY_FIELD not in source.annotations):
        raise ValueError('Copied spell physical faces require their authoritative creation identity')
    values=host._copyable_characteristics(source)
    if mode!=1:return values
    if {'instant','sorcery'}.intersection(host._type_parts(str(values.get('type_line') or ''))[0]):
        return single_face_copy_values(values)
    if source.object_kind=='spell_copy' and DEFINED_TOKEN_IDENTITY_FIELD in source.annotations:
        view=replace(source,is_token=True,object_kind='token',annotations=deepcopy(source.annotations))
        reader=CharacteristicReadHost(host,CharacteristicStateView(host.state,
            ChainMap({view.object_id:view},host.state.cards),host.state.players))
        return reader._copyable_characteristics(view)
    return token_creation_copy_faces(host,source,values)


def copied_object_annotations(host,source,values,*,object_kind,zone):
    """Prepare immutable-creation metadata for the existing copy-object owner."""
    annotations={'copy_overrides':values,**({'copied_from':source.object_id}
        if source is not None else {'token_characteristics':values})}
    if (object_kind=='spell_copy' and zone=='stack' and token_copy_value_mode(host)==1
            and DEFINED_TOKEN_FACES_FIELD in values):
        from .defined_token_faces import defined_token_creation_identity
        annotations.update(defined_token_creation_identity(values))
        annotations.update(token_copy_value_annotations(host))
    return annotations


def copied_object_active_face(host,source,values,*,object_kind,zone):
    from .entry_copy import COPIABLE_ORACLE_FIELD
    if anonymous_face_down_values(host,values):return None
    if (object_kind=='spell_copy' and zone=='stack' and token_copy_value_mode(host)==1
            and DEFINED_TOKEN_FACES_FIELD in values):
        definition=DefinedTransformingToken.from_dict(values[DEFINED_TOKEN_FACES_FIELD])
        return definition.current_face(values.get(DEFINED_TOKEN_ACTIVE_FACE_FIELD)).name
    return source.active_face if source is not None else None


def copied_object_name(host,source,values):
    if anonymous_face_down_values(host,values) or token_copy_value_mode(host)==1 and 'name' in values:
        if type(values.get('name')) is not str:raise ValueError('Copied object name must be its exact characteristic string')
        return values['name']
    return str(values.get('name') or (source.printed_name if source is not None else 'Copy'))


def copied_object_oracle_id(host,source,values,ref):
    from .entry_copy import COPIABLE_ORACLE_FIELD
    if (anonymous_face_down_values(host,values)
            or token_copy_value_mode(host)==1 and str(values.get(COPIABLE_ORACLE_FIELD) or '').startswith('custom-copy:')):
        return 'custom-copy:'+host._stable_runtime_id('copy-oracle',ref)
    return source.oracle_id if source is not None else 'custom-copy:'+host._stable_runtime_id('copy-oracle',ref)
