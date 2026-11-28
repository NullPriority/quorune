"""Closed chosen-X payment from the activating player's hand."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value

ANNOUNCED_HAND_DISCARD_KIND = "discard_announced_hand"
ANNOUNCED_HAND_DISCARD_CAPABILITY = "activation.selected_zone_change.announced_hand_discard"


def announced_hand_discard_predicate():
    return ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True)


def validate_announced_hand_discard_choice(choice):
    if (type(choice.count) is not int or choice.count != 0 or choice.zone != "hand" or choice.another
            or any(getattr(choice, field) is not None for field in (
                "card_type", "counter_amount", "tap_schema_version", "return_schema_version",
                "sacrifice_schema_version", "predicate_union",
            )) or not isinstance(choice.predicate, Mapping)
            or ObjectQuerySpec.from_dict(choice.predicate) != announced_hand_discard_predicate()):
        raise ValueError("Announced hand discard requires a zero-minimum selection of owned hand cards")


def announced_hand_discard_choice(ability):
    return next((choice for choice in ability.choices if choice.kind == ANNOUNCED_HAND_DISCARD_KIND), None)


def validate_announced_hand_discard_ability(ability):
    choice = announced_hand_discard_choice(ability)
    if choice is None:
        return
    if (ability.zones != ("battlefield",) or ability.choices != (choice,) or not any(ability.mana.values())
            or any(getattr(ability, field) for field in (
                "tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source",
                "mana_ability", "uncompiled_costs", "complex_symbols", "life_payment", "energy_payment",
                "random_discard_payment", "mana_cost_options", "waterbend", "loyalty_x", "mill_library_cost",
            )) or any(getattr(ability, field) is not None for field in (
                "announced_x_symbols", "loyalty_delta", "source_counter_removal_cost",
                "source_counter_placement_cost", "variable_colored_reveal_price", "ability_local_mana_reduction",
            ))):
        raise ValueError("Announced hand discard requires ordinary fixed mana and its selected X price")


def announced_hand_discard_activation_dependencies(program):
    """Bind the selected price to the same announced X and original result."""
    from ..abilities import ActivatedAbility
    from ..fixed_top_exile import ANNOUNCED_OPPONENT_FREE_PLAY_SCOPE, announced_opponent_free_play_effect
    from ..discover_model import QUALIFIED_SCAN_TARGET
    from ..replacement.immutable import FrozenMap
    from ..semantic_runtime.activated_abilities import is_structural_activated_ability_catalog_program

    if is_structural_activated_ability_catalog_program(program):
        return ()
    price = program.cost_schema
    catalog = tuple(handler for handler in program.handlers if handler.get("handler_id") == "activation.catalog.pinned.v1")
    descriptor = catalog[0].get("ability", {}) if len(catalog) == 1 else {}
    marked_price = isinstance(price, Mapping) and any(
        isinstance(row, Mapping) and row.get("k") == ANNOUNCED_HAND_DISCARD_KIND for row in price.get("choices", ()))
    marked_catalog = isinstance(descriptor, Mapping) and any(
        isinstance(row, Mapping) and row.get("kind") == ANNOUNCED_HAND_DISCARD_KIND
        for row in descriptor.get("choices", ()))
    marked_result = any(effect.get("scope") == ANNOUNCED_OPPONENT_FREE_PLAY_SCOPE for effect in program.effects)
    if not any((marked_price, marked_catalog, marked_result)):
        return ()
    if (not (marked_price and marked_catalog and marked_result)
            or (program.event, program.active_zone) != ("activate", "battlefield")
            or len(program.handlers) != 1 or len(program.effects) != 1
            or not isinstance(program.target_schema, Mapping)
            or FrozenMap(program.target_schema) != QUALIFIED_SCAN_TARGET):
        raise ValueError("Announced discard and opponent exile require one original targeted battlefield activation")
    ability = ActivatedAbility.from_dict(thaw_value(descriptor))
    choice = announced_hand_discard_choice(ability)
    if (choice is None or program.ability_id != f"ability:{ability.ability_id}"
            or thaw_value(price.get("choices")) != [choice.compact()]
            or thaw_value(price.get("mana")) != dict(ability.mana)
            or FrozenMap(program.effects[0]) != FrozenMap(announced_opponent_free_play_effect())):
        raise ValueError("The same announced X must bind the original discard price and resolution")
    return (ANNOUNCED_HAND_DISCARD_CAPABILITY, "activation.selected_zone_change.fixed")
