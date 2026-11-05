from __future__ import annotations

"""Immutable copiable faces for compiler-defined transforming tokens.

These values describe faces only. Creation, counters, activation authority and
face mutation remain with their canonical owners.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from .replacement.immutable import FrozenMap,thaw_value
from .token_copy_value_model import TOKEN_COPY_VALUE_VERSION_FIELD,token_copy_value_version
import re


DEFINED_TOKEN_FACES_FIELD = 'defined_transforming_token'
DEFINED_TOKEN_ACTIVE_FACE_FIELD = 'defined_token_active_face'
DEFINED_TOKEN_COPY_EXCEPTIONS_FIELD = 'defined_token_copy_exceptions'
DEFINED_TOKEN_IDENTITY_FIELD = 'defined_token_faces'
DEFINED_TOKEN_UNIFORM_COPY_FIELD = 'defined_token_uniform_copy_values'
INCUBATOR_TRANSFORM_PROFILE = 'two_transform_source_v1'
DEFINED_TOKEN_TRANSFORM_KEY = 'builtin:defined-token-transform-source'


def _exact(value, fields, name):
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f'{name} has a closed schema')


@dataclass(frozen=True, slots=True)
class DefinedTokenFace:
    name: str
    type_line: str
    power: str | None = None
    toughness: str | None = None
    ability_profile: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or any(type(value) is not str or not value or value.strip() != value
                       for value in (self.name, self.type_line))):
            raise ValueError('Defined token face requires canonical identity and type data')
        card_types = self.type_line.casefold().replace('—', '-').split('-', 1)[0].split()
        if (not {'artifact', 'creature', 'enchantment', 'land', 'planeswalker', 'battle'}.intersection(card_types)
                or {'instant', 'sorcery'}.intersection(card_types)):
            raise ValueError('Defined token faces must represent permanents')
        if 'creature' in card_types:
            if any(type(value) is not str or value == '-0' or re.fullmatch(r'-?(?:0|[1-9][0-9]*)', value) is None
                   for value in (self.power, self.toughness)):
                raise ValueError('Defined token creature statistics must be fixed integers')
        elif self.power is not None or self.toughness is not None:
            raise ValueError('Noncreature token faces do not define creature statistics')
        if self.ability_profile not in {None, INCUBATOR_TRANSFORM_PROFILE}:
            raise ValueError('Defined token ability profile is unrepresented')

    def characteristics(self):
        result = {'name':self.name,'mana_cost':'','mana_value':0,'type_line':self.type_line,
                  'colors':[],'keywords':[],'produced_mana':[],
                  'power':self.power,'toughness':self.toughness,'display_text':'',
                  'executable_oracle_text':''}
        if self.ability_profile is not None:
            result['activated_ability_profile'] = self.ability_profile
            result['display_text'] = '{2}: Transform this token.'
        return result

    def to_dict(self):
        return {'schema_version':1,'name':self.name,'type_line':self.type_line,
                'power':self.power,'toughness':self.toughness,'ability_profile':self.ability_profile}

    @classmethod
    def from_dict(cls, value):
        _exact(value, {'schema_version','name','type_line','power','toughness','ability_profile'},
               'Defined token face')
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class CopiableTokenFace:
    """One physical orientation and its complete compiler-backed copy values."""
    name: str
    values: FrozenMap

    def __post_init__(self):
        from .abilities import ActivatedAbility
        from .ability_fragments import canonical_ability_fragments
        if type(self.name) is not str or not self.name or self.name!=self.name.strip():
            raise ValueError('Copied physical face requires a canonical orientation identity')
        values=validate_uniform_copy_values(self.values)
        for field in ('name','type_line'):
            if type(values.get(field)) is not str or not values[field] or values[field]!=values[field].strip():
                raise ValueError('Copied physical face requires complete named characteristic values')
        for field in ('colors','keywords','produced_mana'):
            if not isinstance(values.get(field),(list,tuple)) or any(type(value) is not str for value in values[field]):
                raise ValueError('Copied physical face requires typed characteristic arrays')
        if type(values.get('mana_cost')) is not str or type(values.get('mana_value')) not in {int,float} or values['mana_value']<0:
            raise ValueError('Copied physical face requires its copiable mana cost and value')
        for field in ('power','toughness','loyalty','defense'):
            if values.get(field) is not None and type(values[field]) not in {str,int}:
                raise ValueError('Copied physical face statistics must retain their compiled value')
        canonical_ability_fragments(values.get('ability_fragments',()))
        if not isinstance(values.get('activated_abilities'),(list,tuple)):
            raise ValueError('Copied physical face requires its compiled activation catalog')
        for ability in values['activated_abilities']:ActivatedAbility.from_dict(thaw_value(ability))
        object.__setattr__(self,'values',FrozenMap(values))

    @property
    def type_line(self):return self.values['type_line']

    def characteristics(self):return thaw_value(self.values)

    def to_dict(self):return {'schema_version':2,'name':self.name,'characteristics':self.characteristics()}

    @classmethod
    def from_dict(cls,value):
        _exact(value,{'schema_version','name','characteristics'},'Copied token face')
        if type(value['schema_version']) is not int or value['schema_version']!=2:
            raise ValueError('Copied token face requires schema two')
        return cls(value['name'],value['characteristics'])


@dataclass(frozen=True, slots=True)
class DefinedTransformingToken:
    front: DefinedTokenFace | CopiableTokenFace
    back: DefinedTokenFace | CopiableTokenFace
    schema_version: int = 1

    def __post_init__(self):
        face_type=DefinedTokenFace if self.schema_version==1 else CopiableTokenFace
        if (type(self.schema_version) is not int or self.schema_version not in {1,2}
                or not isinstance(self.front, face_type)
                or not isinstance(self.back, face_type)
                or self.front.name == self.back.name):
            raise ValueError('Defined transforming token requires two distinct typed faces')

    def current_face(self, active_face: str | None):
        if active_face is None or active_face == self.front.name:
            return self.front
        if active_face == self.back.name:
            return self.back
        raise ValueError('Defined token active face changed')

    def other_face(self, active_face: str | None):
        return self.back if self.current_face(active_face) == self.front else self.front

    def to_dict(self):
        return {'schema_version':self.schema_version,'front':self.front.to_dict(),'back':self.back.to_dict()}

    @classmethod
    def from_dict(cls, value):
        _exact(value, {'schema_version','front','back'}, 'Defined transforming token')
        face_type=DefinedTokenFace if value['schema_version']==1 else CopiableTokenFace
        return cls(face_type.from_dict(value['front']),face_type.from_dict(value['back']),
                   value['schema_version'])


def incubator_definition():
    """CR111.10i: names are data; the explicit profile supplies the ability."""
    return DefinedTransformingToken(
        DefinedTokenFace('Incubator','Token Artifact — Incubator',ability_profile=INCUBATOR_TRANSFORM_PROFILE),
        DefinedTokenFace('Phyrexian Token','Token Artifact Creature — Phyrexian','0','0'),
    )


def defined_token_characteristic_values(card):
    """A current copy effect supersedes the original token characteristics."""
    overrides = card.annotations.get('copy_overrides') or {}
    original = card.annotations.get('token_characteristics') or {}
    current = token_copy_value_version(card.annotations.get(TOKEN_COPY_VALUE_VERSION_FIELD)) == 1
    if not (current or DEFINED_TOKEN_FACES_FIELD in overrides or DEFINED_TOKEN_FACES_FIELD in original
            or DEFINED_TOKEN_IDENTITY_FIELD in card.annotations):
        return card.annotations.get('object_characteristics') or original or overrides
    return (card.annotations.get('object_characteristics')
           or overrides or original)


def defined_token_definition(card):
    """Physical faces are established by creation, never by a copy effect."""
    raw = card.annotations.get(DEFINED_TOKEN_IDENTITY_FIELD)
    if not card.is_token or raw is None:
        return None
    return DefinedTransformingToken.from_dict(raw)


def defined_token_current_characteristics(card, values=None):
    """Select the copied face by physical orientation, or freeze one face."""
    values = dict(defined_token_characteristic_values(card) if values is None else values)
    if DEFINED_TOKEN_FACES_FIELD not in values:
        return values
    from .standard_token_abilities import standard_token_characteristics
    physical = defined_token_definition(card)
    selected = None
    if physical is not None:
        copied = DefinedTransformingToken.from_dict(values[DEFINED_TOKEN_FACES_FIELD])
        selected = (copied.front if physical.current_face(card.active_face) == physical.front
                    else copied.back).name
    return standard_token_characteristics(values, active_face=selected)


_FACE_METADATA_FIELDS = frozenset({DEFINED_TOKEN_FACES_FIELD, DEFINED_TOKEN_ACTIVE_FACE_FIELD,
    DEFINED_TOKEN_COPY_EXCEPTIONS_FIELD, DEFINED_TOKEN_IDENTITY_FIELD, DEFINED_TOKEN_UNIFORM_COPY_FIELD})


def single_face_copy_values(values):
    """CR707.8: an entry or ongoing copy effect copies the face currently up."""
    from .standard_token_abilities import standard_token_characteristics
    result = (standard_token_characteristics(values) if DEFINED_TOKEN_FACES_FIELD in values
              else deepcopy(dict(values)))
    return {key:value for key,value in result.items() if key not in _FACE_METADATA_FIELDS}


def defined_token_copiable_characteristics(card, values):
    """CR707.8a transports physical faces only for creation of token copies."""
    physical = defined_token_definition(card)
    if physical is None:
        return single_face_copy_values(values)
    if DEFINED_TOKEN_FACES_FIELD in values:
        return defined_token_current_characteristics(card, values)
    result = deepcopy(dict(values))
    result[DEFINED_TOKEN_UNIFORM_COPY_FIELD] = deepcopy(result)
    result[DEFINED_TOKEN_FACES_FIELD] = physical.to_dict()
    result[DEFINED_TOKEN_ACTIVE_FACE_FIELD] = physical.current_face(card.active_face).name
    return result


def defined_token_creation_identity(values):
    """Only the token creation planner may establish physical face identity."""
    if DEFINED_TOKEN_FACES_FIELD not in values:
        return {}
    definition = DefinedTransformingToken.from_dict(values[DEFINED_TOKEN_FACES_FIELD])
    definition.current_face(values.get(DEFINED_TOKEN_ACTIVE_FACE_FIELD))
    if definition.schema_version==2:
        from .standard_token_abilities import standard_token_characteristics
        faces=[]
        for face in (definition.front,definition.back):
            resolved=standard_token_characteristics(values,active_face=face.name)
            faces.append(CopiableTokenFace(face.name,{key:value for key,value in resolved.items() if key not in _FACE_METADATA_FIELDS}))
        definition=DefinedTransformingToken(*faces,schema_version=2)
    return {DEFINED_TOKEN_IDENTITY_FIELD:definition.to_dict()}


def validate_uniform_copy_values(values):
    forbidden = _FACE_METADATA_FIELDS | {'counters','owner','controller','zone','active_face',
        'transform_count','logical_object_id','zone_timestamp','marked_damage','tapped','face_down',
        'is_token','activated_ability_profile'}
    if not isinstance(values,Mapping) or any(type(key) is not str or key in forbidden for key in values):
        raise ValueError('Uniform copied face values must contain only resolved copiable characteristics')
    return deepcopy(dict(values))
