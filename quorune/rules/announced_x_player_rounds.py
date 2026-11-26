"""Exact paid-X quantity for life loss, discard, and two sacrifice rounds."""

from collections.abc import Mapping

from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, DECLARED_AMOUNT_CAPABILITY, CastXAmountSpec
from .effect_shape_walkers import nested_effects
from .fixed_player_rounds import PLAYER_ROUND_SLOTS, validate_fixed_player_rounds


ANNOUNCED_X_PLAYER_ROUNDS_CAPABILITY = "resolution.effect_program.announced_player_rounds"


def uses_announced_x_player_round_choice(effect):
    count = effect.get("count")
    return (effect.get("op") == "choose_cards_apnap" and isinstance(count, Mapping)
            and count.get("kind") == CAST_X_AMOUNT_KIND)


def is_zero_announced_x_player_round(raw, effect):
    """Skip only this admitted APNAP stage when the sealed cast X is zero."""
    return uses_announced_x_player_round_choice(raw) and effect.get("count") == 0


def announced_x_player_round_effects(fixed):
    """Use the same sealed X for every already owned fixed step."""
    validate_fixed_player_rounds(fixed)
    return tuple({**effect, field: CastXAmountSpec().to_dict()}
                 for effect, field in zip(fixed, PLAYER_ROUND_SLOTS, strict=True))


def project_announced_x_player_round_effects(effects, target_schema):
    """Project only this complete sequence into the existing bound-effect owner."""
    if target_schema is not None or len(effects) != len(PLAYER_ROUND_SLOTS):
        raise ValueError("Announced player rounds are an untargeted four-step instruction")
    fixed = []
    for effect, field in zip(effects, PLAYER_ROUND_SLOTS, strict=True):
        if not isinstance(effect, Mapping) or CastXAmountSpec.from_dict(effect[field]) != CastXAmountSpec():
            raise ValueError("All announced player rounds require the same unmodified cast X")
        fixed.append({**effect, field: 1})
    validate_fixed_player_rounds(fixed)
    return tuple(fixed)


def announced_x_player_rounds_program_dependencies(program):
    if not any(uses_announced_x_player_round_choice(effect)
               for effect in nested_effects((*program.effects, *program.handlers))):
        if ANNOUNCED_X_PLAYER_ROUNDS_CAPABILITY not in program.capability_dependencies:
            return ()
    if program.event != "resolve" or program.active_zone != "stack" or program.handlers:
        raise ValueError("Announced player rounds require the complete spell resolution")
    fixed = project_announced_x_player_round_effects(program.effects, program.target_schema)
    from .bound_effect_program_shapes import bound_effect_program_node_capabilities

    owned = bound_effect_program_node_capabilities(
        effects=fixed, target_schema=None, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Announced player rounds lack the existing bound effect owner")
    return (ANNOUNCED_X_PLAYER_ROUNDS_CAPABILITY, DECLARED_AMOUNT_CAPABILITY, *owned)
