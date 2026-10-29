from __future__ import annotations

"""CR700.14: one immutable crossing of paid spell-mana totals."""

from dataclasses import dataclass

EXPEND_CAPABILITY = 'trigger.event.expend'
SPELL_MANA_PAYMENT_EVENT = 'spell.mana_paid'


@dataclass(frozen=True, slots=True)
class SpellManaPaymentEvent:
    player: str
    mana_before: int
    mana_after: int
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported spell-mana payment event version')
        if type(self.player) is not str or not self.player:
            raise ValueError('Spell-mana payment requires its player')
        if (type(self.mana_before) is not int or type(self.mana_after) is not int
                or not 0 <= self.mana_before < self.mana_after):
            raise ValueError('Spell-mana payment requires increasing nonnegative totals')

    def crosses(self, threshold):
        if type(threshold) is not int or threshold <= 0:
            raise ValueError('Expend thresholds are positive integers')
        return self.mana_before < threshold <= self.mana_after

    def to_context(self):
        return {'schema_version': self.schema_version, 'player': self.player,
            'mana_before': self.mana_before, 'mana_after': self.mana_after}

    @classmethod
    def from_context(cls, value):
        if set(value) != {'schema_version', 'player', 'mana_before', 'mana_after'}:
            raise ValueError('Spell-mana payment event fields changed')
        return cls(**value)
