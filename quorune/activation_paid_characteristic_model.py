"""Versioned facts for one creature sacrificed to an activated ability."""
from collections.abc import Mapping
from dataclasses import dataclass

from .scalar_effect_amount_model import PaidCreatureStatSnapshot

ACTIVATION_PAID_STAT_CAPABILITY = 'quantity_expression.paid_activation_creature_stat'
ACTIVATION_PAID_STAT_MECHANIC = 'paid-activation-creature-stat'
ACTIVATION_PAID_STAT_CONTEXT = 'activation_paid_creature_stat'


@dataclass(frozen=True, slots=True)
class ActivationPaidCreatureFact:
    semantic_key: str
    source_logical_object_id: str
    paid_ref: str
    paid_logical_object_id: str
    stats: PaidCreatureStatSnapshot
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or any(type(v) is not str or not v for v in (self.semantic_key,
                    self.source_logical_object_id, self.paid_ref, self.paid_logical_object_id))
                or not isinstance(self.stats, PaidCreatureStatSnapshot)
                or self.stats.logical_object_id != self.paid_logical_object_id):
            raise ValueError('Paid activation stats require the exact paid creature and source identities')

    def to_dict(self):
        return {'schema_version': 1, 'semantic_key': self.semantic_key,
            'source_logical_object_id': self.source_logical_object_id,
            'paid_ref': self.paid_ref, 'paid_logical_object_id': self.paid_logical_object_id,
            'stats': self.stats.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'semantic_key',
                'source_logical_object_id', 'paid_ref', 'paid_logical_object_id', 'stats'}:
            raise ValueError('Paid activation stat fields are incomplete or unknown')
        return cls(**{**value, 'stats': PaidCreatureStatSnapshot.from_dict(value['stats'])})


def resolve_activation_paid_creature_stat(item, characteristic):
    fact = ActivationPaidCreatureFact.from_dict(item.context.get(ACTIVATION_PAID_STAT_CONTEXT))
    if (item.kind != 'activated_ability' or item.semantic_key != fact.semantic_key
            or item.context.get('source_logical_object_id') != fact.source_logical_object_id
            or item.context.get('cost_objects') != [fact.paid_ref]
            or characteristic not in {'power', 'toughness'}):
        raise ValueError('Paid activation stat fact changed its ability or paid cost')
    return getattr(fact.stats, characteristic)
