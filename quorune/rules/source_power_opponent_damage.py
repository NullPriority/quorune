"""One source's power dealt to all opponents after its combat or spell-target occurrence."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..creature_power_damage_model import (
    SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY, source_power_opponent_damage_effect,
    uses_source_power_opponent_damage, validate_source_power_opponent_damage,
)
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects
from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
from .target_announcements import (
    TARGET_ANNOUNCEMENT_CAPABILITY, TARGET_ANNOUNCEMENT_EVENT, source_targeted_by_spell_condition,
)


SOURCE_POWER_OPPONENT_DAMAGE_MECHANIC = "source-power-damage-each-opponent"
SOURCE_POWER_OPPONENT_DAMAGE_VARIANT = "source_attacks_blocks_or_spell_target"
SOURCE_POWER_OPPONENT_DAMAGE_TEMPLATE = "fixed-source-combat-or-spell-target-power-damage-v1"
SOURCE_POWER_OPPONENT_DAMAGE_BODY = "it deals damage equal to its power to each opponent."
SOURCE_POWER_OPPONENT_DAMAGE_DEPENDENCIES = (
    SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY, "damage.creature_power.fixed_interaction",
    "damage.amount.positive", "damage.result.player_life", "damage.source.stack_current_or_lki",
    "trigger.effect.fixed_event", "trigger.placement.apnap", "trigger.subscription.fixed_multi_event",
    "trigger.event.normalized_public_action", TARGET_ANNOUNCEMENT_CAPABILITY,
)


def source_combat_or_spell_target_condition():
    source = {"field": "card", "op": "eq", "value": "$source.ref"}
    return fixed_event_subscription_condition((
        FixedEventSubscription("creature.attacks", source),
        FixedEventSubscription("creature.blocks", source),
        FixedEventSubscription(TARGET_ANNOUNCEMENT_EVENT, source_targeted_by_spell_condition()),
    ))


def source_power_opponent_damage_node_capabilities(effects, target_schema, mechanics, cost_schema=None):
    if (target_schema is not None or cost_schema is not None or len(effects) != 1
            or SOURCE_POWER_OPPONENT_DAMAGE_MECHANIC not in mechanics):
        return ()
    try:
        validate_source_power_opponent_damage(effects[0])
    except (TypeError, ValueError):
        return ()
    return SOURCE_POWER_OPPONENT_DAMAGE_DEPENDENCIES


def source_power_opponent_damage_program_dependencies(program):
    effects = tuple(program.effects)
    matches = tuple(effect for effect in nested_effects((effects, program.handlers))
                    if uses_source_power_opponent_damage(effect))
    if (not matches and SOURCE_POWER_OPPONENT_DAMAGE_MECHANIC not in program.coverage
            and SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY not in program.capability_dependencies):
        return ()
    required = source_power_opponent_damage_node_capabilities(
        effects, program.target_schema, program.coverage, program.cost_schema)
    if (not required or matches != effects or program.handlers
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, "fixed_multi_event_subscription"} <= set(program.coverage)
            or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(source_combat_or_spell_target_condition())):
        raise ValueError("Source-power opponent damage requires its exact attack, block and spell-target subscriptions")
    return required


def is_closed_source_power_opponent_damage_program(program):
    try:
        required = source_power_opponent_damage_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
