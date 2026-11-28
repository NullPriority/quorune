"""An untargeted sorcery activation untaps current creatures before an added combat and main."""

from collections.abc import Mapping

from ..extra_combat_model import (
    CONTROLLED_UNTAP_COMBAT_CAPABILITY, EXTRA_COMBAT_CAPABILITY, ExtraCombatUntap,
    extra_combat_effect, uses_controlled_main_combat,
)
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


CONTROLLED_COMBAT_DEPENDENCIES = (
    CONTROLLED_UNTAP_COMBAT_CAPABILITY, "permanent.tap_state.fixed_set", EXTRA_COMBAT_CAPABILITY,
)


def ordinary_sorcery_combat_activation(ability):
    return (
        ability.zones == ("battlefield",) and ability.sorcery_speed is True
        and ability.compiled_cost and not ability.mana_ability and ability.target_schema is None
    )


def controlled_main_combat_node_capabilities(effects, target_schema, cost_schema):
    if (target_schema is not None or not isinstance(cost_schema, Mapping) or not cost_schema or len(effects) != 1):
        return ()
    effect = effects[0]
    try:
        expected = extra_combat_effect(ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR)
        from ..extra_combat_model import decode_extra_combat_effect
        return (CONTROLLED_COMBAT_DEPENDENCIES if FrozenMap(effect) == FrozenMap(expected)
                and decode_extra_combat_effect(effect) is ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR else ())
    except (TypeError, ValueError):
        return ()


def controlled_main_combat_runtime_dependencies(program):
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    if not any(map(uses_controlled_main_combat, nested_effects((program.effects, program.handlers)))):
        return ()
    required = controlled_main_combat_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or len(program.handlers) != 1):
        raise ValueError("Controlled creature untap and combat require their complete sorcery-timed activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (not ordinary_sorcery_combat_activation(ability) or ability.builtin_semantic_key != program.key
            or program.cost_schema.get("mana") != ability.mana):
        raise ValueError("Controlled creature untap and combat require their original ordinary activated price and timing")
    return required
