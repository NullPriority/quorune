"""Bind one ordinary tap loot and the original source's owner-relative transformed return."""

from ..linked_exile_return_model import (
    LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION, LINKED_TRANSFORMED_OWNER_RETURN_VERSION, LinkedExileReturnSpec,
)
from ..replacement.immutable import thaw_value
from .owner_library_position_shapes import has_exact_tap_price


LOOT_TRANSFORM_CAPABILITY = "activation.resolution.loot_graveyard_transform"
LOOT_TRANSFORM_MECHANIC = "loot-graveyard-threshold-transformed-return"
LOOT_TRANSFORM_BINDING = "blink:loot-graveyard-transform"


def loot_transform_effects():
    from ..compiler.affected_player_discard_templates import standalone_controller_discard_effect_template

    discard = standalone_controller_discard_effect_template("Discard a card.")
    spec = LinkedExileReturnSpec(timing="immediate", controller="owner", transformed=True,
                               schema_version=LINKED_TRANSFORMED_OWNER_RETURN_VERSION).to_dict()
    link = {"op":LINKED_EXILE_RETURN_OPERATION, "binding_id":LOOT_TRANSFORM_BINDING, "spec":spec}
    return ({"op":"draw", "player":"$controller", "count":1, "private":True}, *discard[1],
            {**link,"phase":"exile","cards":"$source.zone_object"}, {**link,"phase":"return"})


def loot_transform_dependencies(effects, target_schema, active_zone, cost_schema):
    if (target_schema is not None or active_zone != "battlefield" or not has_exact_tap_price(cost_schema)
            or thaw_value(effects) != thaw_value(loot_transform_effects())):
        return ()
    from .affected_player_discard_capability_shapes import fixed_controller_discard_node_capabilities
    from ..compiler.affected_player_discard_templates import standalone_controller_discard_effect_template
    from .linked_exile_return_shapes import linked_exile_return_node_capabilities

    discard = fixed_controller_discard_node_capabilities(
        effects=effects[1:2], target_schema=None,
        mechanic_ids=standalone_controller_discard_effect_template("Discard a card.")[3],
    )
    linked = linked_exile_return_node_capabilities(
        effects=effects[2:], target_schema=None, mechanic_ids=(LINKED_EXILE_RETURN_MECHANIC,),
    )
    return (LOOT_TRANSFORM_CAPABILITY, "zone.draw.library_to_hand", *discard, *linked) if discard and linked else ()


def loot_transform_runtime_dependencies(program):
    if (LOOT_TRANSFORM_CAPABILITY not in program.capability_dependencies
            and not any(effect.get("binding_id") == LOOT_TRANSFORM_BINDING for effect in program.effects)):
        return ()
    required = loot_transform_dependencies(program.effects, program.target_schema, program.active_zone, program.cost_schema)
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
    from ..compiler.activated_costs import activated_ability_cost

    if (not required or program.event != "activate" or program.event_condition is not None
            or program.provenance.get("granted_only") or len(program.handlers) != 1):
        raise ValueError("Loot and transformation require the original ordinary tap activation.")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (ability.builtin_semantic_key != program.key or ability.target_schema is not None
            or not has_exact_tap_price(activated_ability_cost(ability))):
        raise ValueError("Loot and transformation changed the original activation price.")
    return required


def bind_loot_transform(host, item, effect):
    if effect.get("binding_id") != LOOT_TRANSFORM_BINDING:
        return effect
    program = host.semantics.get(item.semantic_key)
    source = host.state.cards.get(item.source_object_id)
    instruction = {key:value for key,value in effect.items() if key not in {"reason","_replacement_selections"}}
    if instruction.get("phase") == "exile" and source is not None and instruction.get("cards") == source.ref:
        instruction["cards"] = "$source.zone_object"
    if (item.kind != "activated_ability" or item.context.get("currently_resolving") is not True or item.targets
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not loot_transform_runtime_dependencies(program) or instruction not in program.effects):
        raise ValueError("Loot and transformation lost their original resolving source.")
    if effect["phase"] == "return":
        return effect if LOOT_TRANSFORM_BINDING in item.context.get("linked_exile_returns", {}) else None
    player = host.state.players[item.controller]
    if (source is None or source.zone != "battlefield" or source.phased_out or not player.in_game
            or source.logical_object_id != item.context.get("source_logical_object_id")
            or len(player.zones["graveyard"]) < 5):
        return None
    return effect


def is_closed_loot_transform_or_graveyard_cast_program(program):
    from .artifact_graveyard_cast_activation import artifact_graveyard_cast_runtime_dependencies
    from .loyalty_graveyard_cast import loyalty_graveyard_cast_runtime_dependencies

    try:
        required = (*loot_transform_runtime_dependencies(program), *loyalty_graveyard_cast_runtime_dependencies(program),
                    *artifact_graveyard_cast_runtime_dependencies(program))
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
