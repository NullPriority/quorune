from __future__ import annotations

"""One simultaneous creature damage set for a retained defending player."""

from collections.abc import Mapping

from ..compiler.optional_effect_templates import FIXED_OPTIONAL_EFFECT_CAPABILITY, OPTIONAL_EFFECT_OPERATION
from ..event_player_modifier_model import DEFENDING_PLAYER_REFERENCE
from ..fixed_damage_set_model import (
    DEFENDING_CREATURE_DAMAGE_GROUP_KIND,
    DefendingCreatureDamageGroup,
    FixedDamageSetSpec,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .event_player_result_shapes import _source_combat_player_scope


DEFENDING_CREATURE_DAMAGE_CAPABILITY = "damage.batch.defending_player_creatures"
_DAMAGE_GROUP_DEPENDENCIES = (
    DEFENDING_CREATURE_DAMAGE_CAPABILITY,
    "damage.amount.positive",
    "damage.batch.fixed_set",
    "damage.result.multitype_permanent",
    "damage.source.stack_current_or_lki",
)


def defending_creature_damage_effect(amount):
    if type(amount) is not int or amount < 1:
        raise ValueError("Defending-player creature damage requires a positive fixed amount")
    group = DefendingCreatureDamageGroup(DEFENDING_PLAYER_REFERENCE)
    return {"op": "damage_fixed_set", "source": "$source", "amount": amount,
            **FixedDamageSetSpec((group,)).to_dict()}


def is_defending_creature_damage(effect):
    groups = effect.get("groups")
    return (effect.get("op") == "damage_fixed_set" and isinstance(groups, (list, tuple))
            and any(isinstance(group, Mapping) and group.get("kind") == DEFENDING_CREATURE_DAMAGE_GROUP_KIND
                    for group in groups))


def defending_creature_damage_capabilities(effect, target_schema):
    amount = effect.get("amount")
    if target_schema is not None or type(amount) is not int or amount < 1:
        return ()
    try:
        FixedDamageSetSpec.from_dict({"groups": effect.get("groups")})
    except (TypeError, ValueError):
        return ()
    return _DAMAGE_GROUP_DEPENDENCIES if thaw_value(effect) == defending_creature_damage_effect(amount) else ()


def defending_creature_damage_runtime_dependencies(program):
    values = (program.effects, getattr(program, "handlers", ()))
    if not any(is_defending_creature_damage(effect) for effect in nested_effects(values)):
        return ()
    if ((program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or not _source_combat_player_scope(program.event_condition)
            or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
            or len(program.effects) != 1 or getattr(program, "handlers", ())):
        raise ValueError("Defending-player creature damage requires one exact source attack")
    effect = program.effects[0]
    optional = ()
    if effect.get("op") == OPTIONAL_EFFECT_OPERATION:
        children = effect.get("effects")
        if (set(effect) != {"op", "player", "effects"} or effect["player"] != "$controller"
                or not isinstance(children, (list, tuple)) or len(children) != 1):
            raise ValueError("Defending-player damage requires one ordinary optional result")
        effect = children[0]
        optional = (FIXED_OPTIONAL_EFFECT_CAPABILITY,)
    required = defending_creature_damage_capabilities(effect, target_schema=None)
    if not required:
        raise ValueError("Defending-player damage requires its exact current creature set")
    return (*required, *optional, "trigger.event.normalized_self_attack", "trigger.placement.apnap")
