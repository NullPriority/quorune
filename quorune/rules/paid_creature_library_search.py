"""Closed library search using one paid creature's battlefield mana value."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..library_search_model import PAID_CREATURE_SEARCH_VERSION
from ..replacement.immutable import thaw_value
from ..scalar_effect_amounts import resolve_scalar_effect_amount
from ..scalar_effect_amount_model import (
    PAID_SACRIFICE_MANA_VALUE_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec,
)
from .casting_additional_costs import fixed_creature_sacrifice_price
from .effect_shape_walkers import nested_effects
from .spell_self_exile import SELF_EXILE, terminal_spell_move

CAPABILITY = "library.search.paid_creature_mana_value_bound"
MECHANIC = "paid-creature-mana-value-library-search"
TEMPLATE = "paid-creature-mana-value-library-search-v1"
SEARCH_CAPABILITIES = (
    CAPABILITY, "library.search.fixed_counted", PAID_SACRIFICE_MANA_VALUE_CAPABILITY,
)
REQUIRED = (*SEARCH_CAPABILITIES, "casting.additional_cost.fixed_sacrifice", SELF_EXILE.capability)
_PAID_VALUE = ScalarEffectAmountSpec(
    ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE, characteristic="mana_value", schema_version=12,
).to_dict()


def is_paid_creature_search(value):
    return value.get("op") == "search" and value.get("schema_version") == PAID_CREATURE_SEARCH_VERSION


def paid_creature_search_effect(*, bound=None):
    selector = {"types": ["creature"]}
    if bound is not None:
        selector["mana_value"] = {"maximum": bound}
    return {
        "op": "search", "schema_version": PAID_CREATURE_SEARCH_VERSION, "zone": "library",
        "selector": selector, "destination": "battlefield", "count": {"minimum": 1, "maximum": 1},
        "reveal": False, "shuffle_after": True,
    }


def validate_paid_creature_search(effect, *, materialized=False):
    bound = None
    if materialized:
        selector = effect.get("selector")
        condition = selector.get("mana_value") if isinstance(selector, Mapping) else None
        bound = condition.get("maximum") if isinstance(condition, Mapping) else None
        if type(bound) is not int or bound < 2:
            raise ValueError("Creature search requires its integer bound from the paid value")
    if type(effect.get("schema_version")) is not int or thaw_value(effect) != paid_creature_search_effect(bound=bound):
        raise ValueError("Creature search requires its fixed private library and battlefield result")


def paid_creature_search_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if MECHANIC not in mechanics and not any(is_paid_creature_search(effect) for effect in nested_effects(effects)):
        return None
    if (MECHANIC not in mechanics or terminal_spell_move(mechanics) != SELF_EXILE
            or len(effects) != 1 or target_schema is not None or not fixed_creature_sacrifice_price(cost_schema)):
        return ()
    try:
        validate_paid_creature_search(effects[0])
    except (ValueError, TypeError, KeyError):
        return ()
    return REQUIRED


def paid_creature_search_program_dependencies(program):
    required = paid_creature_search_node_capabilities(
        effects=(*program.effects, *program.handlers), target_schema=program.target_schema,
        mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if required is None:
        return ()
    suffix = f"+{SELF_EXILE.template}+{TEMPLATE}"
    if (not required or program.event != "resolve" or program.active_zone != "stack"
            or not program.ability_id.startswith("spell:") or program.destination != "exile"
            or program.handlers or program.event_condition is not None
            or not str(program.provenance.get("template_id") or "").endswith(suffix)):
        raise ValueError("Paid creature search requires its exact priced resolving spell and terminal exile")
    return required


def is_closed_paid_creature_search_program(program):
    try:
        required = paid_creature_search_program_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def materialize_paid_creature_search(host, item, effect, *, already_materialized=False):
    try:
        program = host.semantics.get(item.semantic_key)
        if (item.context.get("currently_resolving") is not True or program is None
                or not host.semantic_program_is_current_trusted(program)
                or not is_closed_paid_creature_search_program(program)):
            raise ValueError("Paid creature search lost its original resolving spell")
        bounded = paid_creature_search_effect(bound=resolve_scalar_effect_amount(host, _PAID_VALUE, item) + 2)
        if already_materialized:
            if thaw_value(effect) != bounded:
                raise ValueError("Resumed creature search differs from the committed sacrifice")
        else:
            validate_paid_creature_search(effect)
        return bounded
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
