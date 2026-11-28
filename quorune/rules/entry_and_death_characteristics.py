"""Closed branches for an entering controlled creature result and a typed death return."""

from collections.abc import Mapping

from ..event_card_return import event_battlefield_return_effect
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import contains_nested_key


CREATURE_ENTRY_PUMP_CAPABILITY = "resolution.event_object.other_controlled_creature_pump"
CREATURE_ENTRY_PUMP_MECHANIC = "other-controlled-creature-entry-pump"
DEATH_ENCHANTMENT_RETURN_CAPABILITY = "zone.return.death_as_enchantment"
DEATH_ENCHANTMENT_RETURN_MECHANIC = "death-return-as-enchantment"
DEATH_ENCHANTMENT_RETURN_FIELD = "death_enchantment_return_version"
CREATURE_ENTRY_PUMP_VARIANT = "other_controlled_creature_entry_pump"

NONHAND_ENTRY_PUMP_VARIANT = "source_or_controlled_creature_cast_from_nonhand_entry"
NONHAND_ENTRY_PUMP_CAPABILITY = "trigger.condition.cast_from_nonhand_entry"
NONHAND_ENTRY_PUMP_CONDITION = "entrant_cast_by_controller_from_nonhand"
NONHAND_ENTRY_PUMP_COVERAGE = "cast_from_nonhand_entry_intervening"
DEATH_ENCHANTMENT_RETURN_VARIANT = "source_creature_death_enchantment_return"


def creature_entry_pump_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "card", "op": "ne", "value": "$source.ref"},
    ]}


def nonhand_entry_pump_condition():
    return {"all": [
        {"any": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"all": [
                {"field": "types", "op": "contains_any", "value": ["creature"]},
                {"field": "controller", "op": "eq", "value": "$source.controller"},
            ]},
        ]},
        {"field": NONHAND_ENTRY_PUMP_CONDITION, "op": "eq", "value": True},
    ]}


def creature_entry_pump_effect(power):
    if type(power) is not int or power < 1:
        raise ValueError("An entered creature needs a fixed positive power bonus")
    return {"op": "apply_source_characteristics_until_end_of_turn",
            "card": "$event.entry_object", "power": power, "toughness": 0,
            "keywords": ["Haste"], "set_card_types": None, "set_subtypes": None,
            "set_colors": None, "base_power": None, "base_toughness": None}


def death_enchantment_return_effect():
    return {**event_battlefield_return_effect(tapped=False), DEATH_ENCHANTMENT_RETURN_FIELD: 1}


def death_enchantment_return_capabilities(effects, target_schema):
    if target_schema is not None or len(effects) != 1:
        return ()
    effect, = effects
    if (type(effect.get(DEATH_ENCHANTMENT_RETURN_FIELD)) is not int
            or FrozenMap({key: value for key, value in effect.items() if key != "_replacement_selections"})
        != FrozenMap(death_enchantment_return_effect())):
        return ()
    return (DEATH_ENCHANTMENT_RETURN_CAPABILITY, "zone.return.fixed_event_battlefield",
            "trigger.event.normalized_zone_change", "trigger.effect.fixed_event",
            "trigger.placement.apnap", "zone.change.destination_replacement")


def creature_entry_pump_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or CREATURE_ENTRY_PUMP_MECHANIC not in mechanic_ids:
        return ()
    effect, = effects
    try:
        expected = creature_entry_pump_effect(effect.get("power"))
    except ValueError:
        return ()
    if type(effect.get("toughness")) is not int or FrozenMap(effect) != FrozenMap(expected):
        return ()
    return (CREATURE_ENTRY_PUMP_CAPABILITY, "resolution.event_object.fixed_result",
            "continuous.resolution.fixed_characteristics_until_end_of_turn",
            "combat.attack.haste", "activation.tap_untap_cost.haste",
            "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def creature_entry_pump_runtime_dependencies(program):
    if CREATURE_ENTRY_PUMP_CAPABILITY not in program.capability_dependencies and (
        program.event != "creature.enter" or not any(
            effect.get("op") == "apply_source_characteristics_until_end_of_turn"
            and effect.get("card") == "$event.entry_object" for effect in program.effects
        )
    ):
        return ()
    dependencies = creature_entry_pump_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    nonhand = (NONHAND_ENTRY_PUMP_CAPABILITY in program.capability_dependencies
               or NONHAND_ENTRY_PUMP_COVERAGE in program.coverage)
    expected_event = "permanent.enter" if nonhand else "creature.enter"
    expected_condition = nonhand_entry_pump_condition() if nonhand else creature_entry_pump_condition()
    if (not dependencies or (program.event, program.active_zone) != (expected_event, "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(expected_condition)
            or program.handlers or program.cost_schema is not None):
        raise ValueError("The entering-creature pump requires its exact occurrence and condition")
    if nonhand:
        if not {"intervening_condition", NONHAND_ENTRY_PUMP_COVERAGE} <= set(program.coverage):
            raise ValueError("A nonhand cast entry must recheck the original cast on resolution")
        return (*dependencies, NONHAND_ENTRY_PUMP_CAPABILITY, "trigger.condition.source_entry_cast")
    return dependencies


def death_enchantment_return_runtime_dependencies(program):
    if (DEATH_ENCHANTMENT_RETURN_CAPABILITY not in program.capability_dependencies
            and not contains_nested_key((program.effects, program.handlers), DEATH_ENCHANTMENT_RETURN_FIELD)):
        return ()
    required = death_enchantment_return_capabilities(program.effects, program.target_schema)
    if (not required or (program.event, program.active_zone) != ("creature.dies.self", "battlefield")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("The return must bind to its original actual creature death")
    return required

def verify_enchantment_return_origin(host, item, effect):
    from ..errors import GameRuleError

    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    context = item.context.get("event_context", item.context)
    expected = death_enchantment_return_effect()
    actual = FrozenMap({key: value for key, value in effect.items() if key != "_replacement_selections"})
    selections = effect.get("_replacement_selections")
    if isinstance(selections, (list, tuple)) and selections:
        expected = host._semantic_value(expected, item)
    if (
        actual != FrozenMap(expected)
        or item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
        or program is None or not host.semantic_program_is_current_trusted(program)
        or not death_enchantment_return_runtime_dependencies(program)
        or "creature" not in context.get("types", ()) or context.get("to") != "graveyard"
    ):
        raise GameRuleError("Enchantment-only return requires its committed creature death")
