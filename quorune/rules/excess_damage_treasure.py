from __future__ import annotations

"""Count excess dealt to the original creature from one resolving damage instruction."""

from collections.abc import Mapping
from math import ceil

from ..deathtouch import source_has_deathtouch
from ..errors import GameRuleError
from ..query_effect_amount_model import CastXAmountSpec
from ..replacement.immutable import thaw_value
from .committed_quantity_followups import fixed_followup_capabilities, fixed_quantity_effect, quantity_followup_from_count


EXCESS_TREASURE_FIELD = "excess_damage_treasure"
EXCESS_TREASURE_MECHANIC = "excess-damage-tapped-treasures"
EXCESS_TREASURE_CAPABILITY = "damage.target.excess_tapped_treasures"


def excess_treasure_target_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}


def excess_treasure_effect(token):
    return {"op": "damage", "source": "$source", "target": "$target.0",
            "amount": CastXAmountSpec().to_dict(), EXCESS_TREASURE_FIELD: {"schema_version": 1, "token": token}}


def excess_treasure_recipe(recipe, *, actor="$controller"):
    if (not isinstance(recipe, Mapping) or set(recipe) != {"schema_version", "token"}
            or type(recipe["schema_version"]) is not int or recipe["schema_version"] != 1):
        raise ValueError("Excess damage requires its original tapped Treasure recipe")
    token = recipe["token"]
    if (not isinstance(token, Mapping) or token.get("name") != "Treasure Token"
            or token.get("tapped") is not True or token.get("controller") not in ("$controller", actor)):
        raise ValueError("Excess damage must create the resolving controller's tapped Treasure")
    return fixed_quantity_effect({**token, "controller": "$controller"})


def excess_treasure_base_damage(effect, target_schema, mechanics):
    if (EXCESS_TREASURE_MECHANIC not in mechanics
            or thaw_value(target_schema) != excess_treasure_target_schema()
            or set(effect) != {"op", "source", "target", "amount", EXCESS_TREASURE_FIELD}
            or effect["op"] != "damage" or effect["source"] != "$source" or effect["target"] != "$target.0"
            or type(effect["amount"]) is not int or effect["amount"] <= 0):
        return None
    try:
        recipe = excess_treasure_recipe(effect[EXCESS_TREASURE_FIELD])
    except (TypeError, ValueError):
        return None
    return ({key: value for key, value in effect.items() if key != EXCESS_TREASURE_FIELD},
            (EXCESS_TREASURE_CAPABILITY, *fixed_followup_capabilities(thaw_value(recipe))))


def excess_treasure_runtime_dependencies(program):
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .effect_shape_walkers import nested_effects

    if not any(EXCESS_TREASURE_FIELD in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or not program.ability_id.startswith("spell:")
            or len(program.effects) != 1 or program.handlers or program.cost_schema is not None):
        raise ValueError("Excess damage Treasure requires its original spell")
    effect, = program.effects
    if thaw_value(effect.get("amount")) != CastXAmountSpec().to_dict():
        raise ValueError("Excess damage Treasure requires the original announced X")
    required = fixed_damage_node_capabilities(
        effects=({**effect, "amount": 1},), target_schema=thaw_value(program.target_schema),
        mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Excess damage Treasure requires its original target and token result")
    return required


def is_closed_excess_treasure_program(program):
    try:
        required = excess_treasure_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def committed_creature_lethal_threshold(host, proposal):
    recipient = proposal.recipient
    card = host.state.cards.get(recipient.object_id)
    if (recipient.kind != "permanent" or "creature" not in recipient.types or card is None
            or card.logical_object_id != recipient.logical_object_id):
        raise GameRuleError("Excess damage requires its current creature incarnation")
    toughness = host._numeric_stat(card.object_id, "toughness")
    remaining = max(0, ceil(toughness - card.marked_damage))
    return min(1, remaining) if source_has_deathtouch(proposal.source) else remaining


def excess_treasure_after_damage(recipe, proposal, result, *, lethal_threshold):
    dealt = sum(
        event.dealt_amount for event in result.events
        if event.source_logical_object_id == proposal.source.logical_object_id
        and event.target_logical_object_id == proposal.recipient.logical_object_id
    )
    return quantity_followup_from_count(recipe, max(0, dealt - lethal_threshold))
