"""The complete public opponent decision and its independently owned results."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


FAME_FORTUNE_OPERATION = "choose_fame_or_fortune"
FAME_FORTUNE_CAPABILITY = "choice.opponents.public_fame_or_fortune"
BASE_FIELDS = frozenset({"op", "schema_version", "player", "treasure"})


def fame_fortune_effect():
    from ..compiler.token_templates import fixed_token_creation_effect_template

    treasure = fixed_token_creation_effect_template("Create a Treasure token.").effect
    return {"op": FAME_FORTUNE_OPERATION, "schema_version": 1, "player": "$controller", "treasure": treasure}


def validate_fame_fortune(effect, *, actor=None, caster=None):
    if (not isinstance(effect, Mapping) or set(effect) != BASE_FIELDS
            or type(effect.get("schema_version")) is not int):
        raise ValueError("Fame and Fortune require their complete versioned public choice and rewards")
    expected = fame_fortune_effect()
    if actor is not None:
        if any(type(seat) is not str or not seat or seat.startswith("$") for seat in (actor, caster)):
            raise ValueError("Fame and Fortune require the original resolving controller and current actor")
        expected["player"] = actor
        expected["treasure"] = {**expected["treasure"], "controller": caster}
    if FrozenMap(effect) != FrozenMap(expected):
        raise ValueError("Fame and Fortune require their printed public choice and the caster's Treasure")


def fame_fortune_dependencies():
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
    from ..compiler.token_templates import fixed_token_creation_effect_template
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities

    token = fixed_token_creation_effect_template("Create a Treasure token.")
    return (FAME_FORTUNE_CAPABILITY, "continuous.control.fixed_resolution",
            "continuous.resolution.fixed_characteristics_until_end_of_turn", "permanent.untap.effect",
            *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Haste"], "zone.draw.library_to_hand",
            *fixed_token_creation_node_capabilities(effects=(token.effect,), target_schema=None,
                                                   mechanic_ids=token.mechanics))


def fame_fortune_node_capabilities(*, effects, target_schema, mechanic_ids, **_):
    if "public-fame-or-fortune" not in mechanic_ids or len(effects) != 1 or target_schema is not None:
        return ()
    try:
        validate_fame_fortune(effects[0])
    except (TypeError, ValueError):
        return ()
    return fame_fortune_dependencies()


def fame_fortune_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if effect.get("op") == FAME_FORTUNE_OPERATION)
    if not marked and FAME_FORTUNE_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or marked != tuple(program.effects) or program.target_schema is not None
            or program.handlers or program.cost_schema is not None or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Fame and Fortune need their original untargeted resolving spell")
    validate_fame_fortune(marked[0])
    return fame_fortune_dependencies()



def is_closed_fame_fortune_program(program):
    try:
        requirements = fame_fortune_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(requirements) and set(requirements) <= set(program.capability_dependencies)
