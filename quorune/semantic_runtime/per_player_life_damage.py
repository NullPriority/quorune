"""Read each recipient's life once before handing off one damage batch."""
from ..announced_damage import AllocatedDamageRecipient
from ..fixed_damage_set import snapshot_fixed_damage_set
from ..fixed_damage_set_model import FixedDamageSetSpec, PlayerDamageGroup
from ..per_player_life_damage_model import (
    PerPlayerLifeDamageSpec, BoundPerPlayerLifeDamage, BOUND_PER_PLAYER_LIFE_DAMAGE_KIND,
)
from ..rules.live_modifier_quantities import current_player_life_total


def resolve_per_player_life_damage(host, value, item):
    if value.get('kind') == BOUND_PER_PLAYER_LIFE_DAMAGE_KIND:
        bound = BoundPerPlayerLifeDamage.from_dict(value)
        if bound.controller != item.controller:
            raise ValueError('Bound life-damage controller changed')
        return bound.to_dict()
    spec = PerPlayerLifeDamageSpec.from_dict(value)
    snapshot = snapshot_fixed_damage_set(host, actor=item.controller,
        spec=FixedDamageSetSpec((PlayerDamageGroup(spec.relation),)))
    shares = []
    for recipient in snapshot.recipients:
        amount = spec.fraction.amount(current_player_life_total(host, recipient.ref))
        if amount:
            shares.append(AllocatedDamageRecipient(recipient.ref, None, amount))
    return BoundPerPlayerLifeDamage(item.controller, spec.relation, tuple(shares)).to_dict()
