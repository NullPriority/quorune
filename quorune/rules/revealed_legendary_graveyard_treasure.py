from __future__ import annotations

"""The fixed public legendary selection and its committed graveyard quantity."""

from collections.abc import Mapping

from ..fixed_token_production import predefined_token_identity
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..standard_token_abilities import TOKEN_ABILITY_PROFILE_FIELD
from .committed_quantity_followups import fixed_followup_capabilities, quantity_followup_from_count
from .effect_shape_walkers import nested_effects


CAPABILITY = "library.select.revealed_legendary_graveyard_treasure"
TREASURE_FIELD = "committed_graveyard_treasure"
LOOK_COUNT = 4
LEGENDARY_QUERY = ObjectQuerySpec(zones=("library",), supertypes_all=("legendary",))


def revealed_legendary_treasure_effect(*, player="$controller"):
    return {
        "op": "fixed_library_selection", "player": player,
        "look_count": LOOK_COUNT, "public_reveal": True, "selected_reveal": False,
        "selection_policy": "up_to_matching", "minimum": 0, "maximum": LOOK_COUNT,
        "predicate_groups": [[LEGENDARY_QUERY.to_dict()]],
        "remainder_destination": "graveyard", "remainder_order": "chosen", TREASURE_FIELD: 1,
    }


def validate_revealed_legendary_treasure_effect(effect, *, player="$controller"):
    if (not isinstance(effect, Mapping) or type(effect.get(TREASURE_FIELD)) is not int
            or any(type(effect.get(key)) is not int for key in ("look_count", "minimum", "maximum"))
            or effect.get("public_reveal") is not True or effect.get("selected_reveal") is not False
            or FrozenMap(effect) != FrozenMap(revealed_legendary_treasure_effect(player=player))):
        raise ValueError("The Treasure follow-up requires the complete original public legendary selection")


def _treasure():
    name, characteristics = predefined_token_identity("Treasure", {
        "type_line": "Token Artifact — Treasure",
        "display_text": "{T}, Sacrifice this token: Add one mana of any color.",
        TOKEN_ABILITY_PROFILE_FIELD: "tap_sac_any_color_mana_v1",
    })
    return FrozenMap({"op": "create_token", "controller": "$controller", "name": name,
                      "quantity": 1, "characteristics": characteristics})


def revealed_legendary_treasure_capabilities(effect):
    validate_revealed_legendary_treasure_effect(effect)
    token_capabilities = fixed_followup_capabilities(_treasure())
    if not token_capabilities:
        raise ValueError("The original stock Treasure definition is unavailable")
    return (CAPABILITY, "library.select.fixed_controller", "zone.change.destination_replacement",
            *token_capabilities)


def revealed_legendary_treasure_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.target_schema, program.handlers))
                   if TREASURE_FIELD in effect)
    if not marked and CAPABILITY not in program.capability_dependencies:
        return ()
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    if (len(marked) != 1 or marked != tuple(program.effects) or program.target_schema is not None
            or (program.event, program.active_zone) != ("activate", "battlefield")):
        raise ValueError("The public legendary selection requires its original untargeted loyalty ability")
    ability, = activated_abilities_from_descriptors(program.handlers)
    if (type(ability.loyalty_delta) is not int or ability.loyalty_delta != -3
            or ability.mana_ability or ability.activation_conditions
            or activated_ability_cost(ability) != thaw_value(program.cost_schema)
            or any(ability.mana.values()) or ability.complex_symbols or ability.choices
            or ability.tap_source or ability.untap_source or ability.discard_source
            or ability.sacrifice_source or ability.exile_source or ability.life_payment or ability.energy_payment):
        raise ValueError("The public graveyard result requires its ordinary printed three-loyalty payment")
    return revealed_legendary_treasure_capabilities(marked[0])


def treasure_from_committed_library_graveyard(arrangement, moved, actor):
    initial = {identity.object_id: identity.logical_object_id for identity in arrangement.looked}
    actual = {
        card.object_id for card in moved
        if card.zone == "graveyard" and card.owner == actor and card.is_card_object
        and card.object_id in initial and card.logical_object_id != initial[card.object_id]
    }
    return quantity_followup_from_count(_treasure(), len(actual))
