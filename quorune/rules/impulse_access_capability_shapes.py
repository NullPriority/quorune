from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from ..combat_damage_library_exile import (
    is_combat_damage_cast_exile, is_combat_damage_color_cast_exile, is_combat_damage_exile,
    validate_combat_damage_cast_exile, validate_combat_damage_color_cast_exile, validate_combat_damage_exile,
)
from ..defending_player_library_exile import (
    DEFENDING_PLAYER_EXILE_CAPABILITY, is_defending_player_exile, validate_defending_player_exile,
)
from ..entry_x_amount_model import ENTRY_X_AMOUNT_CAPABILITY, ENTRY_X_AMOUNT_KIND, EntryXAmountSpec
from ..land_entry_library_exile_model import (
    LAND_ENTRY_TOP_EXILE_CAPABILITY, is_land_entry_top_exile, validate_land_entry_top_exile,
)
from .land_entry_library_exile import land_entry_top_exile_node_capabilities
from ..impulse_access_model import (
    IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY, IMPULSE_ACCESS_DECLARED_COUNT_VERSION,
    IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY, SHUFFLED_TOP_FREE_PLAY_VERSION, TOP_FREE_INSTANT_SORCERY_VERSION,
    SPAWN_SETTLEMENT_IMPULSE_VERSION, validate_spawn_settlement_impulse, POWER_COUNTED_IMPULSE_VERSION,
    ImpulseAccessDuration, validate_top_free_instant_sorcery,
    impulse_access_count_is_legal, shuffled_top_free_play_effect, validate_shuffled_top_free_play,
)
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, DECLARED_AMOUNT_CAPABILITY, CastXAmountSpec
from ..library_order_model import SHUFFLE_CAPABILITY, fixed_shuffle_effect
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from ..opponent_library_top_model import (
    OPPONENT_HALF_TOP_EXILE_CAPABILITY, OPPONENT_TOP_EXILE_CAPABILITY, OpponentLibraryTopSpec,
    is_opponent_half_top_exile, is_opponent_top_exile, validate_opponent_half_top_exile,
)

from ..compiler.impulse_access_templates import (
    IMPULSE_ACCESS_CAPABILITY_ID,
    IMPULSE_ACCESS_MECHANIC_ID,
)


SHUFFLED_TOP_FREE_PLAY_CAPABILITY = "activation.resolution.shuffled_top_free_play"
SHUFFLED_TOP_FREE_PLAY_MECHANIC = "shuffled-top-free-play-activation"
SHUFFLED_TOP_FREE_PLAY_CAPABILITIES = (
    SHUFFLED_TOP_FREE_PLAY_CAPABILITY, SHUFFLE_CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID,
)
_SHUFFLED_TOP_PRICE = {
    "text": "{5}", "mana": {"GENERIC": 5, **dict.fromkeys("WUBRGC", 0)},
    "complex_symbols": [], "tap_source": False, "untap_source": False,
    "discard_source": False, "sacrifice_source": False, "exile_source": False,
    "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
    "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def shuffled_top_free_play_effects():
    return fixed_shuffle_effect(), shuffled_top_free_play_effect()


def _is_shuffled_top_free_play(effect):
    return effect.get("op") == "fixed_impulse_access" and effect.get("schema_version") == SHUFFLED_TOP_FREE_PLAY_VERSION


def _shuffled_top_free_play_is_closed(effects, target_schema):
    if target_schema is not None or len(effects) != 2 or thaw_value(effects[0]) != fixed_shuffle_effect():
        return False
    try:
        validate_shuffled_top_free_play(thaw_value(effects[1]))
    except ValueError:
        return False
    return True


def shuffled_top_free_play_price_is_closed(cost):
    value = thaw_value(cost)
    return (
        value == _SHUFFLED_TOP_PRICE
        and all(type(value[key]) is type(expected) for key, expected in _SHUFFLED_TOP_PRICE.items())
        and all(type(amount) is int for amount in value["mana"].values())
    )


def shuffled_top_free_play_activation_dependencies(effects, target_schema, active_zone, cost):
    if (
        active_zone != "battlefield" or not shuffled_top_free_play_price_is_closed(cost)
        or not _shuffled_top_free_play_is_closed(effects, target_schema)
    ):
        return ()
    return SHUFFLED_TOP_FREE_PLAY_CAPABILITIES


def shuffled_top_free_play_runtime_dependencies(program):
    if (
        SHUFFLED_TOP_FREE_PLAY_CAPABILITY not in program.capability_dependencies
        and SHUFFLED_TOP_FREE_PLAY_MECHANIC not in program.coverage
        and not any(_is_shuffled_top_free_play(effect) for effect in nested_effects((program.effects, program.handlers)))
    ):
        return ()

    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    required = shuffled_top_free_play_activation_dependencies(
        program.effects, program.target_schema, program.active_zone, program.cost_schema,
    )
    if (
        not required or program.event != "activate" or program.event_condition is not None
        or program.trigger_limit is not None or program.provenance.get("granted_only")
        or len(program.handlers) != 1
    ):
        raise ValueError("Shuffled top free play requires its complete ordered mana-paid activation")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        raise ValueError("Shuffled top free play requires the ordinary activation catalog")
    ability, = abilities
    if (
        ability.builtin_semantic_key != program.key or ability.zones != ("battlefield",)
        or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
        or ability.target_schema is not None or not shuffled_top_free_play_price_is_closed(activated_ability_cost(ability))
    ):
        raise ValueError("Shuffled top free play lost its exact activation price or scope")
    return required


def declared_impulse_access_dependencies(effect):
    if effect.get("op") != "fixed_impulse_access":
        return ()
    if _is_shuffled_top_free_play(effect):
        validate_shuffled_top_free_play(effect)
        return SHUFFLED_TOP_FREE_PLAY_CAPABILITIES
    if effect.get("schema_version") == POWER_COUNTED_IMPULSE_VERSION:
        from .power_counted_impulse import DEPENDENCIES, validate_effect

        validate_effect(effect)
        return DEPENDENCIES
    if effect.get("schema_version") == SPAWN_SETTLEMENT_IMPULSE_VERSION:
        from .impulse_spawn_settlement import DEPENDENCIES

        validate_spawn_settlement_impulse(effect)
        return DEPENDENCIES
    if effect.get("schema_version") == TOP_FREE_INSTANT_SORCERY_VERSION:
        from .loyalty_spell_results import CAPABILITY
        validate_top_free_instant_sorcery(effect)
        return CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID
    if is_combat_damage_color_cast_exile(effect):
        from .combat_damage_library_access import COMBAT_DAMAGE_COLOR_CAST_LIBRARY_DEPENDENCIES

        validate_combat_damage_color_cast_exile(effect)
        return COMBAT_DAMAGE_COLOR_CAST_LIBRARY_DEPENDENCIES
    if is_combat_damage_cast_exile(effect):
        from .combat_damage_library_access import COMBAT_DAMAGE_CAST_LIBRARY_DEPENDENCIES

        validate_combat_damage_cast_exile(effect)
        return COMBAT_DAMAGE_CAST_LIBRARY_DEPENDENCIES
    if is_combat_damage_exile(effect):
        from .combat_damage_library_access import COMBAT_DAMAGE_LIBRARY_DEPENDENCIES

        validate_combat_damage_exile(effect)
        return COMBAT_DAMAGE_LIBRARY_DEPENDENCIES
    if is_defending_player_exile(effect):
        validate_defending_player_exile(effect)
        return DEFENDING_PLAYER_EXILE_CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID
    if is_opponent_half_top_exile(effect):
        validate_opponent_half_top_exile(effect)
        return OPPONENT_HALF_TOP_EXILE_CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID, "target.revalidate_resolution"
    if is_land_entry_top_exile(effect):
        validate_land_entry_top_exile(effect)
        return LAND_ENTRY_TOP_EXILE_CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID
    if is_opponent_top_exile(effect):
        OpponentLibraryTopSpec.from_effect(effect)
        return OPPONENT_TOP_EXILE_CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID, "target.revalidate_resolution"
    count = effect.get("count")
    if "schema_version" not in effect:
        if isinstance(count, Mapping):
            raise ValueError("A declared impulse count requires its instruction version")
        return ()
    if (set(effect) != {"op", "schema_version", "player", "count", "duration"}
            or type(effect["schema_version"]) is not int or effect["schema_version"] != IMPULSE_ACCESS_DECLARED_COUNT_VERSION
            or effect["player"] != "$controller"
            or effect["duration"] not in {"until_end_of_turn", "until_end_of_next_turn"}
            or not isinstance(count, Mapping)):
        raise ValueError("Declared impulse access requires a closed controller result")
    required = (IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY, DECLARED_AMOUNT_CAPABILITY)
    if count.get("kind") == CAST_X_AMOUNT_KIND and CastXAmountSpec.from_dict(count).coefficient == 1:
        return required
    if count.get("kind") == ENTRY_X_AMOUNT_KIND and EntryXAmountSpec.from_dict(count).coefficient == 1:
        return (*required, ENTRY_X_AMOUNT_CAPABILITY)
    raise ValueError("Impulse access count requires the unscaled cast or sealed entry X")


def next_end_step_impulse_dependencies(effect):
    if effect.get("op") != "fixed_impulse_access" or effect.get("duration") != ImpulseAccessDuration.NEXT_END_STEP.value:
        return ()
    if (set(effect) != {"op", "player", "count", "duration"} or effect["player"] != "$controller"
            or not impulse_access_count_is_legal(effect["count"], 1)):
        raise ValueError("Next-end-step impulse access requires one closed fixed-count result")
    return (IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY)


class FixedImpulseAccessProgram(Protocol):
    effects: tuple[Mapping[str, object], ...]
    target_schema: Mapping[str, object] | None
    coverage: tuple[str, ...]
    event: str | None
    active_zone: str | None
    event_condition: Mapping[str, object] | None
    cost_schema: Mapping[str, object] | None
    handlers: tuple[Mapping[str, object], ...]
    trigger_limit: Mapping[str, object] | None
    capability_dependencies: tuple[str, ...]


def fixed_impulse_access_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one fixed own-library impulse-access instruction."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if SHUFFLED_TOP_FREE_PLAY_MECHANIC in mechanics:
        return SHUFFLED_TOP_FREE_PLAY_CAPABILITIES if _shuffled_top_free_play_is_closed(effects, target_schema) else ()
    if len(effects) == 2:
        from .combat_damage_library_access import combat_damage_treasure_cast_node_capabilities

        if required := combat_damage_treasure_cast_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        ):
            return required
    if effects and is_land_entry_top_exile(effects[0]):
        return land_entry_top_exile_node_capabilities(
            effects=effects, target_schema=target_schema,
        )
    if len(effects) == 1 and is_defending_player_exile(effects[0]):
        from .defending_player_top_exile import defending_player_top_exile_node_capabilities

        return defending_player_top_exile_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    if len(effects) == 1 and (is_opponent_top_exile(effects[0]) or is_opponent_half_top_exile(effects[0])):
        from .targeted_opponent_library_top import opponent_top_exile_node_capabilities

        return opponent_top_exile_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    if (
        IMPULSE_ACCESS_MECHANIC_ID not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    try:
        if special := declared_impulse_access_dependencies(effect) or next_end_step_impulse_dependencies(effect):
            return special
    except (TypeError, ValueError):
        return ()
    if (
        set(effect) != {"op", "player", "count", "duration"}
        or effect.get("op") != "fixed_impulse_access"
        or effect.get("player") != "$controller"
        or type(effect.get("count")) is not int
        or not 1 <= int(effect["count"]) <= 10
        or effect.get("duration")
        not in {"until_end_of_turn", "until_end_of_next_turn"}
    ):
        return ()
    return (IMPULSE_ACCESS_CAPABILITY_ID,)


def is_closed_fixed_impulse_access_program(
    program: FixedImpulseAccessProgram,
) -> bool:
    """Recognize only fixed own-library temporary play-access programs."""

    required = set(
        fixed_impulse_access_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    from .impulse_spawn_settlement import CAPABILITY as SPAWN_SETTLEMENT_CAPABILITY, spawn_program_dependencies

    from .power_counted_impulse import CAPABILITY as POWER_COUNTED_CAPABILITY, program_dependencies

    if POWER_COUNTED_CAPABILITY in required:
        try:
            program_dependencies(program)
        except (KeyError, TypeError, ValueError):
            return False
    if SPAWN_SETTLEMENT_CAPABILITY in required:
        try:
            spawn_program_dependencies(program)
        except (KeyError, TypeError, ValueError):
            return False
    if SHUFFLED_TOP_FREE_PLAY_CAPABILITY in required:
        try:
            shuffled_top_free_play_runtime_dependencies(program)
        except (KeyError, TypeError, ValueError):
            return False
    if LAND_ENTRY_TOP_EXILE_CAPABILITY in required:
        from .land_entry_library_exile import land_entry_top_exile_program_dependencies

        try:
            land_entry_top_exile_program_dependencies(program)
        except ValueError:
            return False
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


__all__ = [
    "fixed_impulse_access_node_capabilities",
    "FixedImpulseAccessProgram",
    "is_closed_fixed_impulse_access_program",
]
