"""Carry one actual Behold exile from the original spell to its entry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity
from .errors import GameRuleError
from .linked_exile_return_model import ExiledObjectIdentity, validate_linked_ability_binding


PAID_BEHELD_EXILE_FIELD = 'paid_beheld_exile'


@dataclass(frozen=True, slots=True)
class PaidBeheldExile:
    spell: ContinuousObjectIdentity
    ability_pair: str
    exiled: ExiledObjectIdentity

    def __post_init__(self):
        validate_linked_ability_binding(self.ability_pair)
        if self.spell.object_id == self.exiled.object_id:
            raise ValueError('A linked Behold payment cannot exile its own spell')

    def to_dict(self):
        return {'schema_version': 1, 'spell': self.spell.to_dict(), 'ability_pair': self.ability_pair,
                'exiled': self.exiled.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'spell', 'ability_pair', 'exiled'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Paid Behold exile requires its closed original-spell receipt')
        return cls(ContinuousObjectIdentity.from_dict(value['spell']), value['ability_pair'],
                   ExiledObjectIdentity.from_dict(value['exiled']))


def bind_paid_beheld_exile(item, card, payment):
    pair, exiled = payment
    if (item.kind != 'spell' or item.card_object_id != card.object_id or card.zone != 'stack'
            or card.is_spell_copy or PAID_BEHELD_EXILE_FIELD in item.context):
        raise GameRuleError('Only the original paid spell can bind its Behold exile')
    fact = PaidBeheldExile(ContinuousObjectIdentity(card.object_id, card.logical_object_id), pair, exiled)
    item.context[PAID_BEHELD_EXILE_FIELD] = fact.to_dict()


def mark_resolving_beheld_exile(host, card, item):
    if PAID_BEHELD_EXILE_FIELD not in item.context or item.kind != 'spell' or card.is_spell_copy:
        return
    receipt = PaidBeheldExile.from_dict(item.context[PAID_BEHELD_EXILE_FIELD])
    if (card.zone != 'stack' or item.card_object_id != card.object_id
            or receipt.spell != ContinuousObjectIdentity(card.object_id, card.logical_object_id)):
        raise GameRuleError('A resolving Behold exile has lost its original spell incarnation')
    from .rules.behold_additional_cost import linked_behold_from_cost_schema

    program = host.semantics.get(item.semantic_key)
    cost = linked_behold_from_cost_schema(program.cost_schema) if program is not None else None
    if cost is None or cost.linked_exile_pair != receipt.ability_pair:
        raise GameRuleError('A resolving Behold exile has lost its original casting price')
    card.annotations[PAID_BEHELD_EXILE_FIELD] = receipt.to_dict()


def commit_beheld_exile_entry(host, card, departure):
    fact = departure.paid_beheld_exile
    if fact is None or card.zone != 'battlefield':
        return
    # The zone owner preserves the stack spell's logical ID for this specific move.
    if (departure.origin != 'stack' or fact.spell.object_id != card.object_id
            or fact.spell.logical_object_id != departure.logical_object_id
            or card.logical_object_id != departure.logical_object_id):
        raise GameRuleError('The Behold exile requires the original spell’s successful entry')
    subject = host.state.cards.get(fact.exiled.object_id)
    if subject is None or not fact.exiled.matches_exiled_card(subject):
        return
    from .linked_ability_exile_state import record_linked_ability_exile

    record_linked_ability_exile(host, source=ContinuousObjectIdentity(card.object_id, card.logical_object_id),
                               ability_pair=fact.ability_pair, objects=(fact.exiled,))
