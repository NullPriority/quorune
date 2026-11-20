"""Payload-owned minima for announced-X simultaneous public damage sets."""
from collections.abc import Mapping

from ..fixed_damage_set_model import (
    FixedDamageSetSpec, PermanentControllerRelation, PermanentDamageGroup,
)
from ..query_effect_amount_model import (
    CAST_X_AMOUNT_KIND, DECLARED_AMOUNT_CAPABILITY, CastXAmountSpec,
)


def announced_x_damage_set_dependencies(value):
    """Decode stored result and recipient owners without trusting annotations."""
    amount = value.get('amount')
    if (value.get('op') != 'damage_fixed_set' or not isinstance(amount, Mapping)
            or amount.get('kind') != CAST_X_AMOUNT_KIND):
        return ()
    # These compositions have their own closed payload readers. This owner
    # covers the ordinary set instruction, without changing their contracts.
    from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD
    from ..damage_death_exile_model import DAMAGE_DEATH_EXILE_FIELD
    if {ANNOUNCED_DAMAGE_EFFECT_FIELD, DAMAGE_DEATH_EXILE_FIELD,
            'fixed_target_controller_damage'}.intersection(value):
        return ()
    if set(value) != {'op', 'source', 'amount', 'groups'} or value['source'] != '$source':
        raise ValueError('Announced-X damage requires its closed source and recipient set')
    spec = CastXAmountSpec.from_dict(amount)
    if spec.coefficient < 1:
        raise ValueError('Announced-X damage requires a positive coefficient')
    recipients = FixedDamageSetSpec.from_dict({'groups': value['groups']})
    targeted = any(isinstance(group, PermanentDamageGroup)
        and group.controller_relation is PermanentControllerRelation.TARGET_PLAYER
        for group in recipients.groups)
    target = {'zones': ['player'], 'categories': ['player'], 'count': 1} if targeted else None
    from .damage_capability_shapes import fixed_damage_node_capabilities
    required = fixed_damage_node_capabilities(
        effects=({**value, 'amount': spec.coefficient},), target_schema=target,
        mechanic_ids=('cr-120-damage', 'cr-115-targets') if targeted else ('cr-120-damage',))
    if not required:
        raise ValueError('Announced-X damage lacks its fixed-set result owner')
    return tuple(sorted({DECLARED_AMOUNT_CAPABILITY, *required}))
