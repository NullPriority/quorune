"""The closed battlefield activation that redirects the next one damage."""

from collections.abc import Mapping

from ..compiler.direct_target import DirectPermanentTargetSpec
from .effect_shape_walkers import nested_effects


FINITE_DAMAGE_REDIRECTION_CAPABILITY = "damage.redirection.next_point_to_controlled_creature"
FINITE_DAMAGE_REDIRECTION_MECHANIC = "fixed-next-point-damage-redirection"
FINITE_DAMAGE_REDIRECTION_TEMPLATE = "fixed-next-point-damage-redirection-v1"


def finite_damage_redirection_instruction():
    return {
        "op": "create_damage_redirection",
        "source": "$source",
        "subject": "$source",
        "destination": "$target.0",
        "maximum_damage": 1,
    }


def finite_damage_redirection_target():
    return DirectPermanentTargetSpec(
        types_any=("creature",), controller_relation="you",
    ).to_target_schema()


def uses_finite_damage_redirection(effect):
    return effect.get("op") == "create_damage_redirection" and "maximum_damage" in effect


def _has_free_activation_price(cost):
    if not isinstance(cost, Mapping):
        return False
    expected = {
        "text": "{0}",
        "mana": {color: 0 for color in ("B", "C", "G", "GENERIC", "R", "U", "W")},
        "complex_symbols": [],
        "tap_source": False,
        "untap_source": False,
        "discard_source": False,
        "sacrifice_source": False,
        "exile_source": False,
        "life_payment": 0,
        "energy_payment": 0,
        "loyalty_delta": None,
        "source_counter_removal_cost": None,
        "choices": [],
        "uncompiled_costs": [],
    }
    return cost == expected and all(
        type(cost[field]) is type(expected[field])
        for field in ("life_payment", "energy_payment", "tap_source", "untap_source",
                      "discard_source", "sacrifice_source", "exile_source")
    ) and all(type(value) is int for value in cost["mana"].values())


def finite_damage_redirection_node_capabilities(*, effects, target_schema, mechanics, active_zone, cost):
    if (
        FINITE_DAMAGE_REDIRECTION_MECHANIC in mechanics
        and active_zone == "battlefield"
        and _has_free_activation_price(cost)
        and target_schema == finite_damage_redirection_target()
        and type(target_schema["count"]) is int
        and tuple(effects) == (finite_damage_redirection_instruction(),)
        and type(effects[0]["maximum_damage"]) is int
    ):
        return (FINITE_DAMAGE_REDIRECTION_CAPABILITY, "target.revalidate_resolution")
    return ()


def finite_damage_redirection_runtime_dependencies(program):
    if (
        FINITE_DAMAGE_REDIRECTION_MECHANIC not in program.coverage
        and FINITE_DAMAGE_REDIRECTION_CAPABILITY not in program.capability_dependencies
        and not any(uses_finite_damage_redirection(effect) for effect in
                    nested_effects((*program.effects, *program.handlers)))
    ):
        return ()
    required = finite_damage_redirection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        active_zone=program.active_zone, cost=program.cost_schema,
    )
    if (
        not required or program.event != "activate" or not program.ability_id.startswith("ability:")
        or program.event_condition is not None or not _only_matching_activation_catalog(program)
    ):
        raise ValueError("One-point damage redirection requires the complete battlefield activation")
    return required


def _only_matching_activation_catalog(program):
    from ..compiler.activated_costs import activated_ability_cost
    from ..replacement.immutable import thaw_value
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
    from ..semantic_runtime.context import SemanticNodeError

    if not program.handlers:
        return True
    if len(program.handlers) != 1:
        return False
    try:
        ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    except SemanticNodeError:
        return False
    return (
        ability.builtin_semantic_key == program.key
        and ability.zones == ("battlefield",)
        and not ability.mana_ability
        and thaw_value(ability.target_schema) == program.target_schema
        and activated_ability_cost(ability) == program.cost_schema
    )


def is_closed_finite_damage_redirection(program):
    try:
        required = finite_damage_redirection_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
