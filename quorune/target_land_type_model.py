"""Closed resolution choice for one current land and one basic land type."""
from collections.abc import Mapping
from dataclasses import dataclass
from .basic_land_types import BASIC_LAND_MANA

TARGET_LAND_TYPE_OPERATION = 'choose_target_basic_land_type'
TARGET_LAND_TYPE_CAPABILITY = 'continuous.resolution.target_basic_land_type_choice'
TARGET_LAND_TYPE_MECHANIC = 'target-basic-land-type-choice'
LIMITED_TARGET_LAND_TYPE_CAPABILITY = 'continuous.resolution.target_basic_land_type_limited_choice'
BASIC_LAND_TYPE_CHOICES = tuple(t.title() for t in BASIC_LAND_MANA)


def target_land_type_schema(controlled=False):
    if type(controlled) is not bool:raise ValueError('Land control constraint must be explicit')
    return {'zones':['battlefield'],'categories':['permanent'],'types_any':['land'],'count':1,
        **({'controller_relation':'you'} if controlled else {})}


def target_land_type_schema_is_exact(schema,controlled=False):
    if not isinstance(schema,Mapping) or type(schema.get('count')) is not int:
        return False
    normalized=dict(schema)
    for field in ('zones','categories','types_any'):
        if not isinstance(normalized.get(field),(list,tuple)):
            return False
        normalized[field]=list(normalized[field])
    return normalized==target_land_type_schema(controlled)


def target_land_type_choice_fields(version):
    return ({'op','schema_version','card','player'}
            | ({'controller'} if version==2 else {'choices'} if version==3 else set()))


def target_land_type_options(effect):
    if effect['schema_version'] != 3:
        return BASIC_LAND_TYPE_CHOICES
    choices = effect.get('choices')
    if (not isinstance(choices, (list, tuple)) or len(choices) != 2
            or any(type(choice) is not str or choice not in BASIC_LAND_TYPE_CHOICES for choice in choices)
            or choices[0] == choices[1]):
        raise ValueError('Basic land type choice requires two distinct canonical options')
    return tuple(choices)


def decode_target_land_type(effect, *, materialized=False):
    if not isinstance(effect,Mapping):raise ValueError('Basic land type choice must be an object')
    version=effect.get('schema_version')
    fields=target_land_type_choice_fields(version)
    if (set(effect)!=fields
            or effect['op']!=TARGET_LAND_TYPE_OPERATION
            or type(version) is not int or version not in (1,2,3)):
        raise ValueError('Basic land type choice fields or version changed')
    target_land_type_options(effect)
    card,player=effect['card'],effect['player']
    if materialized:
        if any(type(v) is not str or not v or v.startswith('$') for v in (card,player)):
            raise ValueError('Basic land type choice needs a current object and controller')
    elif (card,player)!=('$target.0','$controller'):
        raise ValueError('Basic land type choice needs its one declared target and resolving controller')
    if version==2 and effect['controller']!=player:
        raise ValueError('Land control must use the resolving controller')
    return card,player


@dataclass(frozen=True,slots=True)
class TargetLandTypeSettingIntent:
    actor: str
    card_ref: str
    object_id: str
    logical_object_id: str
    basic_type: str
    source_stack_ref: str
    reason: str
    source_object_id: str | None = None
    source_logical_object_id: str | None = None
    source_card_ref: str | None = None
    required_controller: str | None = None

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.actor,self.card_ref,self.object_id,
                self.logical_object_id,self.source_stack_ref,self.reason)):
            raise ValueError('Basic land setting intent needs complete current identities')
        if type(self.basic_type) is not str or self.basic_type not in BASIC_LAND_MANA:
            raise ValueError('Choose exactly one canonical basic land type')
        if self.required_controller is not None and (type(self.required_controller) is not str or self.required_controller!=self.actor):
            raise ValueError('A land control constraint must use the resolving controller')
        if (self.source_object_id is None)!=(self.source_logical_object_id is None):
            raise ValueError('Basic land setting needs both source incarnation fields')
        if any(v is not None and (type(v) is not str or not v)
                for v in (self.source_object_id,self.source_logical_object_id,self.source_card_ref)):
            raise ValueError('Basic land setting source identities are malformed')



FIXED_TARGET_LAND_TYPE_VERSION = 24
FIXED_TARGET_LAND_TYPE_CAPABILITY = "continuous.resolution.fixed_target_basic_land_type"
FIXED_TARGET_LAND_TYPE_MECHANIC = "fixed-target-basic-land-type"
SOURCE_PRESENT_LAND_TYPE_CAPABILITY = "continuous.resolution.fixed_target_basic_land_type_source_presence"


@dataclass(frozen=True, slots=True)
class FixedTargetLandTypeSpec:
    basic_type: str
    controller_relation: str
    duration: str
    retain_other_land_types: bool

    def __post_init__(self):
        if type(self.basic_type) is not str or self.basic_type not in BASIC_LAND_MANA:
            raise ValueError("Choose one canonical basic land type")
        if type(self.controller_relation) is not str or self.controller_relation not in ("any", "you", "opponent"):
            raise ValueError("Basic type setting has a closed target relationship")
        if type(self.duration) is not str or self.duration not in ("until_end_of_turn", "zone_object", "until_source_leaves"):
            raise ValueError("Basic type setting requires a represented duration")
        if type(self.retain_other_land_types) is not bool:
            raise ValueError("Basic type setting requires an explicit treatment of existing land types")
        if self.duration == "until_source_leaves" and self.retain_other_land_types:
            raise ValueError("A source-presence land type result requires exact basic type setting")

    def target_schema(self):
        schema = target_land_type_schema()
        if self.controller_relation != "any":
            schema["controller_relation"] = self.controller_relation
        return schema

    def to_dict(self):
        return {
            "basic_type": self.basic_type,
            "controller_relation": self.controller_relation,
            "duration": self.duration,
            "retain_other_land_types": self.retain_other_land_types,
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"basic_type", "controller_relation", "duration", "retain_other_land_types"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("Basic land type setting has a closed descriptor")
        return cls(value["basic_type"], value["controller_relation"], value["duration"],
                   value["retain_other_land_types"])

    def to_effect(self):
        return {
            "op": "apply_source_characteristics_until_end_of_turn",
            "schema_version": FIXED_TARGET_LAND_TYPE_VERSION,
            "card": "$target.0",
            "characteristics": self.to_dict(),
        }

    def layer_operations(self):
        from .continuous_effect_model import ContinuousOperation, Layer

        operation = (ContinuousOperation("add_types", (self.basic_type,), field="subtypes")
                     if self.retain_other_land_types
                     else ContinuousOperation("set_basic_land_types", (self.basic_type,)))
        return ((Layer.TYPE, "4", (operation,)),)
