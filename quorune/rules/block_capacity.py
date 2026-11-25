from __future__ import annotations

"""Read current typed blocking capacity through the characteristic owner."""

from ..block_capacity_model import BlockCapacitySpec
from ..ability_fragments import canonical_ability_fragments
from ..attachments import attached_object_identity
from ..block_capacity_rule_model import ResolvedBlockCapacityRule
from ..continuous_effect_model import ContinuousObjectIdentity


def current_block_capacity(host, card, *, eligible_attackers: int) -> int:
    fragments=canonical_ability_fragments(host._effective_card_data(card).get('ability_fragments',()))
    grants=[f for f in fragments if isinstance(f,BlockCapacitySpec)]
    live = card.zone == "battlefield" and not card.phased_out
    if live:
        identity = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
        grants.extend(effect.capacity for effect in host.state.continuous_effects or ()
            if isinstance(effect, ResolvedBlockCapacityRule) and effect.subject == identity)
    if any(grant.unlimited for grant in grants):
        return eligible_attackers
    extra = sum(grant.additional for grant in grants if grant.schema_version == 1)
    per_equipment = sum(grant.additional for grant in grants if grant.schema_version == 2)
    if per_equipment and live:
        extra += per_equipment * _current_equipment_count(host, card, identity)
    return min(eligible_attackers, 1 + extra)


def _current_equipment_count(host, blocker, identity):
    total = 0
    cards = host.state.cards
    for object_id in dict.fromkeys(blocker.attachments):
        equipment = cards.get(object_id)
        if equipment is None or attached_object_identity(cards, equipment) != identity:
            continue
        card_types, subtypes, _ = host._effective_type_parts(equipment)
        total += int("artifact" in card_types and "equipment" in subtypes)
    return total
