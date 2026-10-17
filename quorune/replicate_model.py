from __future__ import annotations

"""One fixed-mana Replicate instance and its correlated paid count."""

from dataclasses import dataclass
from .repeated_mana_costs import FixedRepeatedManaCostSpec, validate_repeated_mana_payment


REPLICATE_CAPABILITY = 'casting.additional_cost.replicate_fixed_mana'
REPLICATE_HANDLER = 'casting.replicate.fixed-mana.v1'
REPLICATE_FIELD = 'replicate_paid'
REPLICATE_COUNT_FIELD = 'replicate_count'
REPLICATE_SEMANTIC_KEY = 'builtin:replicate'


@dataclass(frozen=True, slots=True)
class FixedManaReplicateSpec(FixedRepeatedManaCostSpec):
    pass


def validate_replicate_fact(value):
    return validate_repeated_mana_payment(value)
