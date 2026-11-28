"""Payload-owned minima for announced-X simultaneous public damage sets."""
from collections.abc import Mapping

from ..fixed_damage_set_model import (
    FixedDamageSetSpec, PermanentControllerRelation, PermanentDamageGroup,
)
from ..query_effect_amount_model import (
    CAST_X_AMOUNT_KIND, DECLARED_AMOUNT_CAPABILITY, SCALED_ANNOUNCED_TARGET_DAMAGE_CAPABILITY, CastXAmountSpec,
)


SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD = "scaled_announced_target_damage_version"
_SCALED_TARGET_CAPABILITIES = (
    SCALED_ANNOUNCED_TARGET_DAMAGE_CAPABILITY, DECLARED_AMOUNT_CAPABILITY,
    "target.cardinality.mana_announced_x", "target.revalidate_resolution", "damage.amount.positive",
    "damage.batch.fixed_set", "damage.source.stack_current_or_lki", "damage.result.player_life",
    "damage.result.multitype_permanent",
)


def scaled_announced_target_damage_effect():
    return {"op": "damage_fixed_set", "source": "$source", SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD: 1,
            "amount": CastXAmountSpec(coefficient=5, schema_version=4).to_dict()}


def scaled_announced_target_damage_schema():
    from ..target_cardinality import AnnouncedTargetCardinality

    return {
        "zones": ["player", "battlefield"], "categories": ["player", "permanent"], "predicate": "damageable",
        "count_parameter": AnnouncedTargetCardinality(selection="up_to").to_dict(),
    }


def _validate_scaled_announced_target_damage(effect):
    if (not isinstance(effect, Mapping) or type(effect.get(SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD)) is not int
            or effect != scaled_announced_target_damage_effect()
            or CastXAmountSpec.from_dict(effect["amount"]).schema_version != 4):
        raise ValueError("Equal damage for each target requires its closed fivefold announced X")


def scaled_announced_target_damage_node_capabilities(effects, target_schema, mechanics):
    from ..replacement.immutable import FrozenMap

    if (len(effects) != 1 or not {"cr-115-targets", "cr-120-damage", "mana-announced-target-set"} <= set(mechanics)
            or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != FrozenMap(scaled_announced_target_damage_schema())):
        return ()
    from ..target_cardinality import AnnouncedTargetCardinality

    try:
        _validate_scaled_announced_target_damage(effects[0])
        AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"])
    except (TypeError, ValueError):
        return ()
    return _SCALED_TARGET_CAPABILITIES


def scaled_announced_target_damage_program_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    if (SCALED_ANNOUNCED_TARGET_DAMAGE_CAPABILITY not in program.capability_dependencies
            and not contains_nested_key((*program.effects, *program.handlers), SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD)):
        return ()
    required = scaled_announced_target_damage_node_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.cost_schema is not None
            or program.handlers or program.event_condition is not None or program.trigger_limit is not None):
        raise ValueError("Equal announced-X damage requires the original spell and up-to-X target set")
    return required


def bind_scaled_announced_target_damage(host, effect, item, *, amount):
    from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, AllocatedDamageRecipient
    from .damage_snapshots import recipient_snapshot

    _validate_scaled_announced_target_damage(effect)
    x = item.x_value
    if (item.kind not in {"spell", "spell_copy"} or type(x) is not int or x < 0 or type(amount) is not int
            or amount != x * 5 or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or len(item.targets) > x):
        raise ValueError("Equal announced-X damage needs the original locked X and revalidated targets")
    refs = tuple(ref for ref in item.targets if ref is not None)
    if any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
        raise ValueError("Equal announced-X damage requires distinct revalidated targets")
    recipients = [AllocatedDamageRecipient(
        ref, recipient_snapshot(host, ref, actor=item.controller).logical_object_id, amount,
    ) for ref in refs if amount]
    return {"op": "damage_fixed_set", "source": "$source", "amount": amount * len(recipients),
            ANNOUNCED_DAMAGE_EFFECT_FIELD: [recipient.to_dict() for recipient in recipients]}


def announced_x_damage_set_dependencies(value):
    """Decode stored result and recipient owners without trusting annotations."""
    if SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD in value:
        _validate_scaled_announced_target_damage(value)
        return _SCALED_TARGET_CAPABILITIES
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
    if spec.coefficient < 1 or spec.schema_version == 4:
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
