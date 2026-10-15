from __future__ import annotations

"""Fixed energy costs use the canonical counter transaction, without replacements."""

from dataclasses import dataclass


ENERGY_PAYMENT_CAPABILITY = 'cost.energy.fixed_resolution_payment'


@dataclass(frozen=True, slots=True)
class PayEnergyIntent:
    actor: str
    player: str
    amount: int
    reason: str

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.actor, self.player, self.reason))
                or self.actor != self.player or type(self.amount) is not int or self.amount <= 0):
            raise ValueError('Energy payment requires a positive fixed cost paid by its actor')
