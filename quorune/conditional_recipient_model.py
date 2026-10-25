from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


CONDITIONAL_RECIPIENT_CAPABILITY = 'continuous.characteristics.conditional_recipients'


@dataclass(frozen=True, slots=True)
class ConditionalRecipientSpec:
    role: str
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or self.role not in {
                'source', 'other_creatures', 'other_land_creatures', 'controlled_creatures', 'unique_creature'}:
            raise ValueError('Conditional recipient roles require one closed versioned role')

    def to_dict(self):
        return {'schema_version': 1, 'role': self.role}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'role'}:
            raise ValueError('Conditional recipient fields are closed')
        return cls(value['role'], value['schema_version'])


def validate_conditional_recipient_descriptor(row):
    from .continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind as Kind
    from .object_predicate import ObjectQuerySpec
    from .characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    spec = ConditionalRecipientSpec.from_dict(row['recipient'])
    condition = FixedPublicStateConditionSpec.from_dict(row['source_condition'])
    if spec.role == 'source':
        expected = {'kind': 'source', 'target_controller': None, 'predicate': None, 'exclude_source': False, 'types_all': []}
    else:
        query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature', 'land') if spec.role == 'other_land_creatures' else ('creature',))
        expected = {'kind': 'fixed_query', 'target_controller': 'source_controller', 'predicate': query.to_dict(),
            'exclude_source': spec.role in {'other_creatures', 'other_land_creatures'}, 'types_all': []}
    if row['target'] != expected:
        raise ValueError('Conditional recipient role and target must agree exactly')
    if spec.role == 'unique_creature':
        quantity = CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=('battlefield',), types_all=('creature',)))
        if condition.kind is not Kind.ALL or {term.kind for term in condition.terms} != {Kind.QUERY_COUNT_AT_LEAST, Kind.QUERY_COUNT_AT_MOST} or any(
                term.amount != 1 or term.quantity != quantity for term in condition.terms):
            raise ValueError('Unique recipient requires the complete exactly-one creature condition')
    return spec, condition


def conditional_recipient_capabilities(value):
    from .continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateFact
    if isinstance(value, Mapping):
        own = set()
        if 'recipient' in value:
            _spec, condition = validate_conditional_recipient_descriptor(value)
            own.add(CONDITIONAL_RECIPIENT_CAPABILITY)
            if any(leaf.fact is FixedPublicStateFact.CONTROLLER_CONTROLS_OWN_COMMANDER for leaf in condition.leaf_conditions()):
                own.add('state_query.commander.designation')
        if value.get('fact') == FixedPublicStateFact.CONTROLLER_CONTROLS_OWN_COMMANDER.value:
            FixedPublicStateConditionSpec.from_dict(value)
            own.update((CONDITIONAL_RECIPIENT_CAPABILITY, 'state_query.commander.designation'))
        return tuple(sorted({*own, *(cap for child in value.values() for cap in conditional_recipient_capabilities(child))}))
    if isinstance(value, (tuple, list)):
        return tuple(sorted({cap for child in value for cap in conditional_recipient_capabilities(child)}))
    return ()
