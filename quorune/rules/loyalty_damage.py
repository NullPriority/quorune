"""Exact ordinary zero-loyalty and minus-X damage result shapes."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..target_combat_damage_multiplier_model import (
    MULTIPLIER_CAPABILITY, MULTIPLIER_MECHANIC, MULTIPLIER_OPERATION, multiplier_instruction,
    TURN_MULTIPLIER_CAPABILITY, turn_multiplier_instruction,
)
from .effect_shape_walkers import contains_nested_key
from .linked_loyalty_exile_model import LOYALTY_X_CAPABILITY


LOYALTY_X_DAMAGE_FIELD = "loyalty_x_target_set_version"
LOYALTY_X_DAMAGE_CAPABILITY = "damage.batch.loyalty_x_each_target"
LOYALTY_X_DAMAGE_MECHANIC = "loyalty-x-damage-each-of-up-to-three-targets"

_CREATURE = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}


def loyalty_damage_target_schema(*, variable):
    if not variable:
        return dict(_CREATURE)
    return {
        "groups": [{
            "zones": ["player", "battlefield"], "categories": ["player", "permanent"], "predicate": "damageable",
            "min": 0, "max": 3,
        }],
    }


def loyalty_x_damage_instruction():
    return {"op": "damage_fixed_set", "source": "$source", LOYALTY_X_DAMAGE_FIELD: 1}


def loyalty_damage_capabilities(*, effects, target_schema, mechanics, active_zone, cost_schema):
    if len(effects) != 1 or active_zone != "battlefield" or not isinstance(effects[0], Mapping):
        return ()
    from .linked_loyalty_exile_shapes import _linked_loyalty_cost_is_closed

    effect = effects[0]
    if (effect == multiplier_instruction() and type(effect["schema_version"]) is int
            and MULTIPLIER_MECHANIC in mechanics
            and _linked_loyalty_cost_is_closed(cost_schema, "zero")
            and isinstance(target_schema, Mapping) and type(target_schema.get("count")) is int
            and FrozenMap(target_schema) == FrozenMap(loyalty_damage_target_schema(variable=False))):
        return (MULTIPLIER_CAPABILITY, "target.revalidate_resolution")
    if not (
        effect == loyalty_x_damage_instruction() and type(effect[LOYALTY_X_DAMAGE_FIELD]) is int
        and LOYALTY_X_DAMAGE_MECHANIC in mechanics
        and _linked_loyalty_cost_is_closed(cost_schema, "choose")
        and isinstance(target_schema, Mapping)
        and FrozenMap(target_schema) == FrozenMap(loyalty_damage_target_schema(variable=True))
    ):
        return ()
    group, = target_schema["groups"]
    if type(group["min"]) is not int or type(group["max"]) is not int:
        return ()
    return (LOYALTY_X_DAMAGE_CAPABILITY, LOYALTY_X_CAPABILITY, "damage.batch.fixed_set", "target.revalidate_resolution")


def turn_combat_multiplier_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 2 or type(effects[1].get("schema_version")) is not int
            or effects[1] != turn_multiplier_instruction()):
        return ()
    from .temporary_keyword_removal import temporary_keyword_removal_capabilities

    removal = effects[0]
    from ..object_predicate import ObjectQuerySpec

    expected = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict()
    predicate = removal.get("predicate", {})
    if (not isinstance(predicate, Mapping) or FrozenMap(predicate) != FrozenMap(expected)
            or tuple(removal.get("remove_keywords", ())) != ("Trample",)
            or removal.get("power") != 0 or removal.get("toughness") != 0 or removal.get("keywords")):
        return ()
    required = temporary_keyword_removal_capabilities(effects=(removal,), target_schema=None, mechanic_ids=mechanic_ids)
    return (*required, MULTIPLIER_CAPABILITY, TURN_MULTIPLIER_CAPABILITY) if required else ()


def loyalty_damage_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effect_operations

    values = (program.effects, program.handlers)
    if any(effect.get("op") == MULTIPLIER_OPERATION and effect.get("schema_version") == 2
           for effect in program.effects):
        required = turn_combat_multiplier_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        from ..card_programs.admission import program_has_declared_complete_source_admission

        if (not required or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers
                or program.cost_schema is not None or not program_has_declared_complete_source_admission(program)):
            raise ValueError("Combat damage multiplication requires the admitted complete turn rule")
        return required
    if not (
        set(program.capability_dependencies).intersection((MULTIPLIER_CAPABILITY, LOYALTY_X_DAMAGE_CAPABILITY))
        or MULTIPLIER_OPERATION in nested_effect_operations(values)
        or contains_nested_key(values, LOYALTY_X_DAMAGE_FIELD)
    ):
        return ()
    from .linked_loyalty_exile_shapes import has_plain_loyalty_catalog

    required = loyalty_damage_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    if not required or not has_plain_loyalty_catalog(program, require_semantic_key=True):
        raise ValueError("Loyalty damage requires the original plain battlefield activation")
    return required


def is_closed_loyalty_damage_program(program):
    try:
        required = loyalty_damage_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def bind_loyalty_x_target_damage(host, effect, item):
    amount = item.x_value
    if (effect != loyalty_x_damage_instruction() or type(effect[LOYALTY_X_DAMAGE_FIELD]) is not int
            or item.kind not in {"activated_ability", "activated_ability_copy"}
            or type(amount) is not int or amount < 0 or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True
            or not 0 <= len(item.targets) <= 3):
        raise ValueError("Loyalty damage needs its exact announced X and revalidated targets")
    return _uniform_announced_damage_set(host, item, amount)


def bind_uniform_announced_target_damage(host, item):
    amount = item.x_value
    if (type(amount) is not int or amount < 0 or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or not 0 <= len(item.targets) <= 3):
        raise ValueError("Announced damage requires its original X and revalidated targets")
    return _uniform_announced_damage_set(host, item, amount)


def _uniform_announced_damage_set(host, item, amount):
    from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, AllocatedDamageRecipient
    from .damage_snapshots import recipient_snapshot

    refs = tuple(ref for ref in item.targets if ref is not None)
    if len(set(refs)) != len(refs):
        raise ValueError("A loyalty damage target must be chosen only once")
    recipients = [
        AllocatedDamageRecipient(ref, recipient_snapshot(host, ref, actor=item.controller).logical_object_id, amount)
        for ref in refs if amount
    ]
    return {
        "op": "damage_fixed_set", "source": "$source", "amount": amount * len(recipients),
        ANNOUNCED_DAMAGE_EFFECT_FIELD: [recipient.to_dict() for recipient in recipients],
    }
