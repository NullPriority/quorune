from __future__ import annotations

"""Read current typed blocking capacity through the characteristic owner."""

from ..block_capacity_model import BlockCapacitySpec
from ..ability_fragments import canonical_ability_fragments
from ..block_capacity_rule_model import ResolvedBlockCapacityRule
from ..continuous_effect_model import ContinuousObjectIdentity


def current_block_capacity(host, card, *, eligible_attackers: int) -> int:
    fragments=canonical_ability_fragments(host._effective_card_data(card).get('ability_fragments',()))
    grants=[f for f in fragments if isinstance(f,BlockCapacitySpec)]
    if card.zone == 'battlefield' and not card.phased_out:
        identity = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
        grants.extend(effect.capacity for effect in host.state.continuous_effects or ()
            if isinstance(effect, ResolvedBlockCapacityRule) and effect.subject == identity)
    if any(f.unlimited for f in grants):return eligible_attackers
    return min(eligible_attackers,1+sum(f.additional for f in grants))
