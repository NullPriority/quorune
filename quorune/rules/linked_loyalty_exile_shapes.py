"""Exact capability and activation scope for linked loyalty choices."""

from collections.abc import Mapping

from ..library_search_model import FixedCountedLibrarySearchTemplate, counted_search_bound_dependencies
from ..query_effect_amount_model import CastXAmountSpec
from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY
from ..linked_exile_return_model import LINKED_EXILE_RETURN_CAPABILITY
from .linked_loyalty_exile_model import (
    LINKED_LOYALTY_CREATURE_ENTRY_CAPABILITY,
    LINKED_LOYALTY_EXILE_FIELD,
    LINKED_LOYALTY_OPPONENT_ZONES_CAPABILITY,
    LINKED_LOYALTY_TOP_EXILE_CAPABILITY,
    LOYALTY_X_CAPABILITY,
    LOYALTY_X_FIELD,
    is_loyalty_x_cost,
    validate_linked_loyalty_exile_instruction,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import contains_nested_key
from .targeted_opponent_library_top import OPPONENT_TOP_EXILE_TARGET


_RESULT_CAPABILITIES = {
    "target_top": (
        LINKED_LOYALTY_TOP_EXILE_CAPABILITY, "activation.loyalty.positive_counter_cost",
        "target.revalidate_resolution",
    ),
    "choose": (LINKED_LOYALTY_CREATURE_ENTRY_CAPABILITY, LOYALTY_X_CAPABILITY),
    "opponent_zones": (LINKED_LOYALTY_OPPONENT_ZONES_CAPABILITY,),
}
_LOYALTY_COST_DEFAULTS = {
    "mana": {"GENERIC": 0, "W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0},
    "complex_symbols": [],
    "tap_source": False,
    "untap_source": False,
    "discard_source": False,
    "sacrifice_source": False,
    "exile_source": False,
    "life_payment": 0,
    "energy_payment": 0,
    "source_counter_removal_cost": None,
    "choices": [],
    "uncompiled_costs": [],
}


def uses_linked_loyalty_activation(effects, cost_schema):
    return contains_nested_key(effects, LINKED_LOYALTY_EXILE_FIELD) or (
        isinstance(cost_schema, Mapping) and LOYALTY_X_FIELD in cost_schema
    )


def _linked_loyalty_cost_is_closed(cost_schema, phase):
    if not isinstance(cost_schema, Mapping) or type(cost_schema.get("loyalty_delta")) is not int:
        return False
    cost_text = cost_schema.get("text")
    expected = {**_LOYALTY_COST_DEFAULTS, "text": cost_text}
    if phase == "zero" and cost_text == "0":
        expected["loyalty_delta"] = 0
    elif phase == "target_top" and cost_text == "+2":
        expected["loyalty_delta"] = 2
    elif phase == "opponent_zones" and cost_text in ("-10", "\u221210"):
        expected["loyalty_delta"] = -10
    elif phase == "choose" and type(cost_text) is str and is_loyalty_x_cost(cost_text):
        if cost_schema.get(LOYALTY_X_FIELD) is not True:
            return False
        expected.update(loyalty_delta=0, loyalty_x=True)
    else:
        return False
    if FrozenMap(cost_schema) != FrozenMap(expected):
        return False
    return (
        all(type(cost_schema[key]) is bool for key in (
            "tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source",
        ))
        and all(type(cost_schema[key]) is int for key in ("life_payment", "energy_payment"))
        and all(type(amount) is int for amount in cost_schema["mana"].values())
    )


def loyalty_artifact_search_activation_capabilities(*, effects, target_schema, active_zone, cost_schema):
    if (len(effects) != 1 or target_schema is not None or active_zone != "battlefield"
            or not _linked_loyalty_cost_is_closed(cost_schema, "choose")):
        return ()
    fixed_search = FixedCountedLibrarySearchTemplate(
        count=1, optional_count=False,
        selector=FrozenMap({"types": ["artifact"]}),
        destination="battlefield", reveal=False,
    ).effect()
    expected = {**fixed_search, "selector": {
        **fixed_search["selector"], "mana_value": {"maximum": CastXAmountSpec().to_dict()},
    }}
    effect, = effects
    if not isinstance(effect, Mapping) or FrozenMap(effect) != FrozenMap(expected):
        return ()
    try:
        required = counted_search_bound_dependencies(effect)
    except (TypeError, ValueError, KeyError):
        return ()
    return (LOYALTY_X_CAPABILITY, *required)


def linked_loyalty_activation_capabilities(*, effects, target_schema, active_zone, cost_schema):
    if len(effects) != 1 or active_zone != "battlefield":
        return ()
    try:
        phase = validate_linked_loyalty_exile_instruction(effects[0])
    except ValueError:
        return ()
    if not _linked_loyalty_cost_is_closed(cost_schema, phase):
        return ()
    if phase == "target_top":
        if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
                or FrozenMap(target_schema) != OPPONENT_TOP_EXILE_TARGET):
            return ()
    elif target_schema is not None:
        return ()
    return (
        *_RESULT_CAPABILITIES[phase],
        LINKED_ABILITY_EXILE_CAPABILITY,
        LINKED_EXILE_RETURN_CAPABILITY,
        "zone.change.destination_replacement",
    )


def linked_loyalty_exile_runtime_dependencies(program):
    from .loyalty_damage import LOYALTY_X_DAMAGE_FIELD, loyalty_damage_runtime_dependencies

    if (len(program.effects) == 1 and LOYALTY_X_DAMAGE_FIELD in program.effects[0]
            and loyalty_damage_runtime_dependencies(program)):
        return ()  # The ordinary damage result owns its complete price and allocation scope.
    claimed = set(getattr(program, "capability_dependencies", ()))
    if (
        claimed.isdisjoint((
            LINKED_LOYALTY_TOP_EXILE_CAPABILITY, LINKED_LOYALTY_OPPONENT_ZONES_CAPABILITY,
            LINKED_LOYALTY_CREATURE_ENTRY_CAPABILITY, LOYALTY_X_CAPABILITY,
        ))
        and not uses_linked_loyalty_activation((program.effects, program.handlers), program.cost_schema)
        and not contains_nested_key(program.handlers, LOYALTY_X_FIELD)
    ):
        return ()
    scope = {
        "effects": program.effects, "target_schema": program.target_schema,
        "active_zone": program.active_zone, "cost_schema": program.cost_schema,
    }
    required = (
        linked_loyalty_activation_capabilities(**scope)
        or loyalty_artifact_search_activation_capabilities(**scope)
    )
    if not required or not has_plain_loyalty_catalog(program):
        raise ValueError("Linked loyalty requires one ordinary battlefield activation and its original price")
    return required


def has_plain_loyalty_catalog(program, *, require_semantic_key=False):
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    abilities = activated_abilities_from_descriptors(program.handlers)
    if (program.event != "activate" or program.event_condition is not None
            or program.trigger_limit is not None or len(program.handlers) != 1 or len(abilities) != 1):
        return False
    ability, = abilities
    return (
        program.ability_id == f"ability:{ability.ability_id}"
        and (not require_semantic_key or ability.builtin_semantic_key == program.key)
        and ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and not ability.activation_conditions
        and ability.target_schema == (
            FrozenMap(program.target_schema) if program.target_schema is not None else None
        )
        and activated_ability_cost(ability) == thaw_value(program.cost_schema)
    )


def is_closed_linked_loyalty_activation_program(program):
    try:
        required = linked_loyalty_exile_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
