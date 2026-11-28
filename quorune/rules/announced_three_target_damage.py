"""Bind announced spell X once for each distinct remaining target."""

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import contains_nested_key
from .legendary_sorcery import CAPABILITY as LEGENDARY_SORCERY_CAPABILITY
from .loyalty_damage import loyalty_damage_target_schema

FIELD = "spell_x_each_target_version"
CAPABILITY = "damage.batch.announced_x_each_target"


def effect():
    return {"op": "damage_fixed_set", "source": "$source", FIELD: 1}


def target_schema():
    return loyalty_damage_target_schema(variable=True)


def node_dependencies(effect_value, targets):
    if (effect_value != effect() or type(effect_value.get(FIELD)) is not int
            or FrozenMap(targets or {}) != FrozenMap(target_schema())):
        return ()
    group, = targets["groups"]
    if type(group.get("min")) is not int or type(group.get("max")) is not int:
        return ()
    return (CAPABILITY, LEGENDARY_SORCERY_CAPABILITY, "damage.batch.fixed_set", "target.revalidate_resolution",
            "damage.amount.positive", "damage.result.player_life", "damage.result.multitype_permanent",
            "damage.source.stack_current_or_lki")


def program_dependencies(program):
    effects = tuple(program.effects)
    if (not contains_nested_key((effects, program.handlers), FIELD)
            and CAPABILITY not in program.capability_dependencies):
        return ()
    if program.event != "resolve" or program.active_zone != "stack" or len(effects) != 1:
        raise ValueError("Announced X damage requires one original resolving spell instruction")
    required = node_dependencies(effects[0], program.target_schema)
    if not required:
        raise ValueError("Announced X damage requires its exact zero-to-three distinct target structure")
    return required


def is_closed_announced_target_damage_program(program):
    try:
        required = program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_spell_x_target_damage(host, value, item):
    from .loyalty_damage import bind_uniform_announced_target_damage

    program = host.semantics.get(item.semantic_key)
    if (value != effect() or type(value.get(FIELD)) is not int or item.kind not in {"spell", "spell_copy"}
            or program is None or not host.semantic_program_is_current_trusted(program)
            or CAPABILITY not in program_dependencies(program)):
        raise ValueError("Announced X damage lost its original admitted resolving spell")
    return bind_uniform_announced_target_damage(host, item)
