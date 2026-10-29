"""Immutable CR702.99 encoded-card and creature incarnation relationship."""
from dataclasses import dataclass
from collections.abc import Mapping
from .continuous_effect_model import ContinuousObjectIdentity

CIPHER_CAPABILITY = 'casting.lifecycle.cipher'
CIPHER_MECHANIC = 'cipher'
CIPHER_HANDLER = 'casting.lifecycle.cipher.v1'
CIPHER_LINK_FIELD = 'cipher_encoding'
CIPHER_ENCODE_OPERATION = 'encode_cipher'
CIPHER_CAST_OPERATION = 'cast_cipher_copy'
CIPHER_TRIGGER_KEY = 'builtin:cipher'
CIPHER_CAST_PRODUCER = 'cipher'
CIPHER_GRANT_HANDLER = 'continuous.ability.cipher-encoding.v1'


@dataclass(frozen=True, slots=True)
class CipherEncoding:
    card: ContinuousObjectIdentity
    creature: ContinuousObjectIdentity
    card_ref: str
    creature_ref: str

    def __post_init__(self):
        if not isinstance(self.card, ContinuousObjectIdentity) or not isinstance(self.creature, ContinuousObjectIdentity):
            raise ValueError('Cipher links require typed original incarnations')
        if self.card.object_id == self.creature.object_id:
            raise ValueError('Cipher card and encoded object must be distinct')
        if any(type(value) is not str or not value or value != value.strip() for value in (self.card_ref, self.creature_ref)):
            raise ValueError('Cipher links require canonical public references')

    def to_dict(self):
        return {'schema_version': 1, 'card': self.card.to_dict(), 'creature': self.creature.to_dict(),
                'card_ref': self.card_ref, 'creature_ref': self.creature_ref}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'card', 'creature', 'card_ref', 'creature_ref'}:
            raise ValueError('Cipher encoding has a closed schema')
        if type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError('Cipher encoding version is unsupported')
        return cls(ContinuousObjectIdentity.from_dict(value['card']), ContinuousObjectIdentity.from_dict(value['creature']),
                   value['card_ref'], value['creature_ref'])


@dataclass(frozen=True, slots=True)
class EncodeCipherIntent:
    actor: str
    card: ContinuousObjectIdentity
    creature: ContinuousObjectIdentity | None
    creature_ref: str | None
    reason: str
    replacement_selections: tuple = ()

    def __post_init__(self):
        if type(self.actor) is not str or not self.actor or not isinstance(self.card, ContinuousObjectIdentity):
            raise ValueError('Cipher encoding intent requires its actor and spell incarnation')
        if (self.creature is None) != (self.creature_ref is None):
            raise ValueError('Cipher decline and creature reference must agree')
        if self.creature is not None and not isinstance(self.creature, ContinuousObjectIdentity):
            raise ValueError('Cipher selected creature requires an original incarnation')


def cipher_candidates(rows, actor):
    return tuple(sorted((row for row in rows if row.zone == 'battlefield' and row.controller == actor
                 and not row.phased_out and 'creature' in row.types), key=lambda row: (row.ref, row.logical_object_id)))


def cipher_intent_identity(intent):
    return {'actor': intent.actor, 'card': intent.card.to_dict(),
            'creature': intent.creature.to_dict() if intent.creature else None,
            'creature_ref': intent.creature_ref, 'reason': intent.reason}


def validate_cipher_intent_identity(value):
    if not isinstance(value, Mapping) or set(value) != {'actor', 'card', 'creature', 'creature_ref', 'reason'}:
        raise ValueError('Cipher intent identity has a closed schema')
    return cipher_intent_identity(EncodeCipherIntent(value['actor'], ContinuousObjectIdentity.from_dict(value['card']),
        ContinuousObjectIdentity.from_dict(value['creature']) if value['creature'] is not None else None,
        value['creature_ref'], value['reason']))
