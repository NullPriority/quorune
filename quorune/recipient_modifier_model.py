from __future__ import annotations

"""Closed recipient-local and shared-type quantities for live layer7c modifiers."""

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from .counter_names import normalized_counter_name

RECIPIENT_MODIFIER_CAPABILITY='continuous.power_toughness.recipient_quantity'
RECIPIENT_MODIFIER_OPERATION='modify_power_toughness_by_recipient_quantity'
RECIPIENT_SHARED_TYPE_CAPABILITY='continuous.power_toughness.recipient_shared_type'
RECIPIENT_SHARED_NAME_CAPABILITY='continuous.power_toughness.recipient_shared_name'


class RecipientQuantityKind(str,Enum):
    COLORS='colors'
    COUNTERS='counters'
    MANA_SYMBOLS='mana_symbols'
    CREATURE_TYPES='creature_types'
    SHARED_CREATURE_TYPES='shared_creature_types'
    OTHER_SAME_NAME_CREATURES='other_same_name_creatures'


@dataclass(frozen=True,slots=True)
class RecipientQuantitySpec:
    kind: RecipientQuantityKind
    counter: str|None=None
    mana_color: str|None=None
    maximum: int|None=None
    schema_version: int=1
    controller_relation: str|None=None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {1,2,3} or not isinstance(self.kind,RecipientQuantityKind):
            raise ValueError('Recipient quantity identity is malformed')
        if self.kind is RecipientQuantityKind.SHARED_CREATURE_TYPES:
            if self.schema_version!=2 or self.controller_relation not in {'any','source_controller'}:
                raise ValueError('Shared creature-type quantities require an explicit count controller')
        elif self.kind is RecipientQuantityKind.OTHER_SAME_NAME_CREATURES:
            if self.schema_version != 3 or self.controller_relation != 'source_controller' or self.maximum is not None:
                raise ValueError('Same-name creatures require the source controller and an uncapped versioned count')
        elif self.schema_version!=1 or self.controller_relation is not None:
            raise ValueError('Only shared quantities carry a controller relation')
        if self.kind is RecipientQuantityKind.COUNTERS:
            if type(self.counter) is not str or normalized_counter_name(self.counter)!=self.counter:
                raise ValueError('Recipient counter must be a canonical fixed counter name')
        elif self.counter is not None:raise ValueError('Only counter quantities name a counter')
        if self.kind is RecipientQuantityKind.MANA_SYMBOLS:
            if type(self.mana_color) is not str or self.mana_color not in {'W','U','B','R','G'}:
                raise ValueError('Recipient mana quantities require one fixed color')
        elif self.mana_color is not None:raise ValueError('Only mana quantities name a color')
        if self.maximum is not None and (type(self.maximum) is not int or self.maximum<1):
            raise ValueError('Recipient quantity maximum must be a positive integer')

    def to_dict(self):
        value={'schema_version':self.schema_version,'kind':self.kind.value,'counter':self.counter,
            'mana_color':self.mana_color,'maximum':self.maximum}
        if self.schema_version in {2,3}:value['controller_relation']=self.controller_relation
        return value

    @classmethod
    def from_dict(cls,value):
        fields={'schema_version','kind','counter','mana_color','maximum'}
        if isinstance(value,Mapping) and value.get('schema_version') in {2,3}:fields.add('controller_relation')
        if not isinstance(value,Mapping) or set(value)!=fields:
            raise ValueError('Recipient quantity fields are malformed')
        return cls(kind=RecipientQuantityKind(value['kind']),counter=value['counter'],
            mana_color=value['mana_color'],maximum=value['maximum'],schema_version=value['schema_version'],
            controller_relation=value.get('controller_relation'))


def validate_recipient_modifier(value):
    fields={'schema_version','quantity','power','toughness'}
    if isinstance(value,Mapping) and value.get('schema_version') in {2,3}:fields.add('objects')
    if not isinstance(value,Mapping) or set(value)!=fields:
        raise ValueError('Recipient modifier fields are malformed')
    if type(value['schema_version']) is not int or value['schema_version'] not in {1,2,3}:
        raise ValueError('Recipient modifier version is malformed')
    if any(type(value[key]) is not int for key in ('power','toughness')) or not(value['power'] or value['toughness']):
        raise ValueError('Recipient modifier requires nonzero integer coefficients')
    spec=RecipientQuantitySpec.from_dict(value['quantity'])
    if value['schema_version']!=spec.schema_version:
        raise ValueError('Recipient modifier and quantity versions must agree')
    if value['schema_version']==2:
        from .shared_creature_type_model import shared_type_objects
        shared_type_objects(value['objects'])
    elif value['schema_version'] == 3:
        same_name_objects(value['objects'])
    return spec



def same_name_objects(values):
    """Read current incarnations and the public rules names of controlled creatures."""
    from .continuous_effect_model import ContinuousObjectIdentity

    if not isinstance(values, (list, tuple)):
        raise ValueError("Same-name quantity requires a sealed object array")
    members = {}
    physical_ids = set()
    for row in values:
        if not isinstance(row, Mapping) or set(row) != {"identity", "names"}:
            raise ValueError("Same-name object fields are malformed")
        identity = ContinuousObjectIdentity.from_dict(row["identity"])
        names = row["names"]
        if (not isinstance(names, (list, tuple)) or any(type(name) is not str or not name.strip() for name in names)
                or tuple(names) != tuple(sorted(set(names)))):
            raise ValueError("Same-name object rules names must be distinct and canonical")
        if identity.object_id in physical_ids:
            raise ValueError("Same-name quantity cannot repeat an original physical object")
        physical_ids.add(identity.object_id)
        members[identity] = frozenset(names)
    return members
