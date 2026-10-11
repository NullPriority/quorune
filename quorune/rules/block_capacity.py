from __future__ import annotations

"""Read current typed blocking capacity through the characteristic owner."""

from ..block_capacity_model import BlockCapacitySpec
from ..ability_fragments import canonical_ability_fragments


def current_block_capacity(host, card, *, eligible_attackers: int) -> int:
    fragments=canonical_ability_fragments(host._effective_card_data(card).get('ability_fragments',()))
    grants=[f for f in fragments if isinstance(f,BlockCapacitySpec)]
    if any(f.unlimited for f in grants):return eligible_attackers
    return min(eligible_attackers,1+sum(f.additional for f in grants))
