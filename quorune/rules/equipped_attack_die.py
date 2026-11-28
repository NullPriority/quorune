from __future__ import annotations

"""One committed d6 and the live power of an original equipped attacker."""

from ..event_counter_reference import resolve_event_counter_recipient
from ..replacement.immutable import FrozenMap
from .attached_greatest_power_condition import _has_maximum_creature_power
from .random_selection import publish_resolution_die_roll


CAPABILITY = "random.die.equipped_attack_growth"
ROLL_FIELD = "equipped_attack_die_growth_version"
DRAW_FIELD = "equipped_attack_greatest_power_version"
ATTACK_EVENT = "creature.attacks"
ATTACK_CONDITION = FrozenMap({"field": "source_attachment_target_ref", "op": "eq", "value": "$context.card"})
GROWTH_MECHANICS = ("cr-611-continuous-effects", "fixed-source-characteristics-until-end-of-turn")
MECHANICS = (*GROWTH_MECHANICS, "cr-121-drawing-a-card")
_EVENT_REQUIREMENTS = ("attachment.reference.current_or_lki", "trigger.effect.fixed_event",
                       "trigger.event.normalized_public_action", "trigger.placement.apnap")
_ATTACKER = "$event.permanent_object"
_SIDES = 6


def _growth(attacker, power):
    return {
        "op": "apply_source_characteristics_until_end_of_turn",
        "card": attacker,
        "set_card_types": None, "set_subtypes": None, "set_colors": None,
        "base_power": None, "base_toughness": None,
        "power": power, "toughness": 0, "keywords": [],
    }


def _draw(controller):
    return {"op": "draw", "player": controller, "count": 1, "private": True}


def equipped_attack_die_effects():
    return ({**_growth(_ATTACKER, 0), ROLL_FIELD: 1},
            {**_draw("$controller"), DRAW_FIELD: 1, "attacker": _ATTACKER})


def equipped_attack_die_declared(value):
    from .effect_shape_walkers import contains_nested_key

    return contains_nested_key(value, ROLL_FIELD) or contains_nested_key(value, DRAW_FIELD)


def equipped_attack_die_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    from .attack_event_results import attack_event_characteristic_capabilities

    expected = equipped_attack_die_effects()
    if (target_schema is not None or cost_schema is not None or len(effects) != 2
            or not set(MECHANICS).issubset(mechanics)
            or tuple(map(FrozenMap, effects)) != tuple(map(FrozenMap, expected))
            or type(effects[0].get(ROLL_FIELD)) is not int or type(effects[1].get(DRAW_FIELD)) is not int
            or type(effects[0].get("power")) is not int or type(effects[0].get("toughness")) is not int
            or type(effects[1].get("count")) is not int or effects[1].get("private") is not True):
        return ()
    growth = attack_event_characteristic_capabilities(
        effects=(_growth(_ATTACKER, 1),), target_schema=None, mechanic_ids=GROWTH_MECHANICS,
    )
    return (CAPABILITY, *growth, "zone.draw.library_to_hand") if growth else ()


def equipped_attack_die_runtime_dependencies(program):
    if not equipped_attack_die_declared((program.effects, program.target_schema, program.cost_schema, program.handlers)):
        return ()
    required = equipped_attack_die_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if (not required or program.handlers or (program.event, program.active_zone) != (ATTACK_EVENT, "battlefield")
            or FrozenMap(program.event_condition or {}) != ATTACK_CONDITION):
        raise ValueError("The equipped attack die requires the original equipped attacker and ordered results")
    return (*required, *_EVENT_REQUIREMENTS)


def bind_equipped_attack_die(host, item, effect, *, instruction_pointer):
    roll = ROLL_FIELD in effect
    index, marker = (0, ROLL_FIELD) if roll else (1, DRAW_FIELD)
    if (FrozenMap(effect) != FrozenMap(equipped_attack_die_effects()[index]) or type(effect.get(marker)) is not int
            or item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
            or item.context.get("event") != ATTACK_EVENT or not item.context.get("source_logical_object_id")):
        raise ValueError("The equipped attack result requires its sealed trigger")
    controller = item.controller
    host._require_seat(controller, in_game=True)
    attacker = resolve_event_counter_recipient(host, item)
    if roll:
        result = publish_resolution_die_roll(
            host, controller=controller, stack_ref=item.ref, instruction_pointer=instruction_pointer, sides=_SIDES,
        )
        return _growth(attacker, result) if attacker is not None else None
    card = next((row for row in host.state.cards.values() if row.ref == attacker), None) if attacker is not None else None
    return _draw(controller) if _has_maximum_creature_power(host, card) else None
