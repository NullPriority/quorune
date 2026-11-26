"""Original target, public top exile and controller-only permission scope."""

from collections.abc import Mapping

from ..compiler.impulse_access_templates import IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_MECHANIC_ID
from ..compiler.spell_cast_predicates import (
    FixedSpellCastCharacteristicKind, FixedSpellCastCharacteristicQuery, FixedSpellCastCharacteristicTerm,
    FixedSpellCastController, FixedSpellCastQuality, FixedSpellCastSubject,
)
from ..opponent_library_top_model import (
    OPPONENT_HALF_TOP_EXILE_CAPABILITY, OPPONENT_TOP_EXILE_CAPABILITY, OpponentLibraryTopSpec,
    is_opponent_half_top_exile, is_opponent_top_exile, validate_opponent_half_top_exile,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


OPPONENT_TOP_EXILE_MECHANICS = (IMPULSE_ACCESS_MECHANIC_ID, "cr-115-targets")
OPPONENT_TOP_EXILE_TARGET = FrozenMap({
    "zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "opponent",
})
OWN_COLORLESS_CAST = FixedSpellCastSubject(
    FixedSpellCastController.SOURCE,
    characteristic_query=FixedSpellCastCharacteristicQuery((
        FixedSpellCastCharacteristicTerm(FixedSpellCastCharacteristicKind.COLORLESS),
    )),
)
OWN_COLORLESS_CAST_CONDITION = FrozenMap(OWN_COLORLESS_CAST.event_condition)
OWN_SPELL_CAST_CONDITION = FrozenMap(FixedSpellCastSubject(
    FixedSpellCastController.SOURCE, quality=FixedSpellCastQuality.ANY, source_spell=True,
).event_condition)


def opponent_top_exile_target_schema():
    return thaw_value(OPPONENT_TOP_EXILE_TARGET)


def opponent_top_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or not isinstance(target_schema, Mapping)
            or not set(OPPONENT_TOP_EXILE_MECHANICS).issubset(mechanic_ids)):
        return ()
    try:
        effect = effects[0]
        is_half = is_opponent_half_top_exile(effect)
        if is_half:
            validate_opponent_half_top_exile(effect)
        else:
            spec = OpponentLibraryTopSpec.from_effect(effect)
        if type(target_schema.get("count")) is not int or FrozenMap(target_schema) != OPPONENT_TOP_EXILE_TARGET:
            return ()
    except (KeyError, TypeError, ValueError):
        return ()
    capability = OPPONENT_HALF_TOP_EXILE_CAPABILITY if is_half else OPPONENT_TOP_EXILE_CAPABILITY
    required = {capability, IMPULSE_ACCESS_CAPABILITY_ID,
                "target.revalidate_resolution", "zone.change.destination_replacement"}
    if is_half or not spec.allows_play:
        required.update(("trigger.event.normalized_spell_cast", "trigger.placement.apnap"))
    return tuple(sorted(required))


def opponent_top_exile_program_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if is_opponent_top_exile(effect) or is_opponent_half_top_exile(effect))
    caps = {OPPONENT_TOP_EXILE_CAPABILITY, OPPONENT_HALF_TOP_EXILE_CAPABILITY}
    if not marked and not caps.intersection(program.capability_dependencies):
        return ()
    if len(marked) != 1 or effects != marked:
        raise ValueError("Opponent top exile requires the complete original single-target result")
    if is_opponent_half_top_exile(effects[0]):
        validate_opponent_half_top_exile(effects[0])
        if ((program.event, program.active_zone) != ("spell.cast", "stack")
                or program.handlers or program.cost_schema is not None
                or FrozenMap(program.event_condition or {}) != OWN_SPELL_CAST_CONDITION):
            raise ValueError("Opponent top-half exile requires the original spell's cast trigger")
    elif program.active_zone != "battlefield":
        raise ValueError("Fixed opponent top exile requires its original battlefield ability")
    elif OpponentLibraryTopSpec.from_effect(effects[0]).allows_play:
        from ..semantic_runtime.activated_abilities import (
            ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
        )

        if (program.event != "activate" or len(program.handlers) != 1
                or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
            raise ValueError("Targeted top play requires its original battlefield activation")
        abilities = activated_abilities_from_descriptors(program.handlers)
        if len(abilities) != 1 or program.ability_id != f"ability:{abilities[0].ability_id}":
            raise ValueError("Targeted top play lost its original activation identity")
    elif (program.event != "spell.cast" or program.handlers or program.cost_schema is not None
          or FrozenMap(program.event_condition or {}) != OWN_COLORLESS_CAST_CONDITION):
        raise ValueError("Unrestricted face-up exile requires the original controller's colorless cast")
    required = opponent_top_exile_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Opponent library exile lost its original target schema")
    return required
