"""Runtime requirements for zone movement, selected subjects, and bound results."""

from __future__ import annotations

from ..rules.hand_inspection_capability_shapes import selected_hand_reveal_discard_runtime_dependencies
from ..rules.paradigm import paradigm_runtime_dependencies
from ..rules.token_copy_recipients import target_copy_recipient_runtime_dependencies
from ..rules.spell_self_exile import (
    conditional_spell_return_runtime_dependencies,
    spell_self_bottom_runtime_dependencies, spell_self_exile_runtime_dependencies, spell_self_shuffle_runtime_dependencies,
)


def target_death_return_runtime_dependencies(program):
    from ..delayed_object_model import (
        is_target_death_battlefield_return, is_target_death_return_effect,
        target_death_return_node_capabilities, target_death_return_price_is_closed,
    )
    from ..replacement.immutable import FrozenMap
    from ..rules.effect_shape_walkers import nested_effects
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    if not any(is_target_death_return_effect(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = target_death_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=set(program.coverage) - {"generated_oracle_ir", "activated_ability"},
    )
    price = program.cost_schema
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None
            or not target_death_return_price_is_closed(price, battlefield=is_target_death_battlefield_return(program.effects[0]))
            or len(program.handlers) != 1 or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Death return requires its exact battlefield activation and target")
    ability, = activated_abilities_from_descriptors(program.handlers)
    catalog_price = {name: getattr(ability, name) for name in price if name != "text"}
    catalog_price["text"] = ability.cost_text
    if (ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
            or ability.zones != ("battlefield",) or ability.target_schema != FrozenMap(program.target_schema)
            or FrozenMap(catalog_price) != FrozenMap(price)
            or any((ability.mana_cost_options, ability.waterbend, ability.boast, ability.reconfigure,
                    ability.forecast, ability.crew_threshold, ability.saddle_threshold, ability.power_up,
                    ability.random_discard_payment, ability.mill_library_cost, ability.activation_limit,
                    ability.granting_attachment_payment, ability.ability_local_mana_reduction,
                    ability.generic_reduction_per_legendary_creature, ability.source_counter_placement_cost,
                    ability.variable_colored_reveal_price, ability.announced_x_symbols))):
        raise ValueError("Death return target or mana price differs from its activation catalog")
    return required



def stack_spell_target_counter_runtime_dependencies(program) -> tuple[str, ...]:
    from ..replacement.immutable import thaw_value
    from ..stack_spell_targeting import (
        CURRENT_TARGET_COUNTER, CURRENT_TARGET_COUNTER_CAPABILITIES, StackSpellTargetPredicate,
        contains_stack_spell_target_predicate, stack_spell_target_predicate,
    )

    schema = program.target_schema
    if not contains_stack_spell_target_predicate(schema):
        return ()
    predicate = stack_spell_target_predicate(schema)
    event_zone = (program.event, program.active_zone)
    if predicate is None or event_zone not in {("resolve", "stack"), ("activate", "battlefield")}:
        raise ValueError("A spell-target subject requires a closed counter resolution or activation")
    if predicate is StackSpellTargetPredicate.SOURCE and program.event != "activate":
        raise ValueError("A current-source subject requires a battlefield activation")
    effects = tuple(thaw_value(effect) for effect in program.effects)
    if not effects or effects[0] != CURRENT_TARGET_COUNTER:
        raise ValueError("A spell-target subject requires the ordinary counter as its first result")
    if len(effects) == 1:
        return CURRENT_TARGET_COUNTER_CAPABILITIES
    if (program.event != "resolve" or len(effects) != 2
            or effects[1] != {"op": "draw", "player": "$controller", "count": 1, "private": True}
            or type(effects[1].get("count")) is not int or effects[1].get("private") is not True):
        raise ValueError("A counter with a spell-target subject permits only one private draw afterward")
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies

    draw = composed_spell_family_runtime_dependencies(
        program, capability="zone.draw.library_to_hand", mechanic="cr-121-drawing-a-card",
    )
    if not draw:
        raise ValueError("The counter and private draw require exact printed composition")
    return (*CURRENT_TARGET_COUNTER_CAPABILITIES, *draw)



def _announced_zone_activation_checks():
    from ..rules.announced_artifact_sacrifice_return import announced_artifact_sacrifice_program_dependencies
    from ..rules.announced_graveyard_copy import announced_graveyard_copy_runtime_dependencies

    return (
        (announced_graveyard_copy_runtime_dependencies, "announced_graveyard_self_copy"),
        (announced_artifact_sacrifice_program_dependencies, "announced_artifact_sacrifice_return"),
    )



def _library_search_runtime_requirements(program, required, blockers):
    from ..rules.affected_library_search_batch import exile_land_search_runtime_dependencies
    from ..rules.search_random_discard import private_search_random_discard_runtime_dependencies

    try:
        required.update(exile_land_search_runtime_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_exile_land_search:invalid:{exc}")

    try:
        required.update(private_search_random_discard_runtime_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_private_search_random_discard:invalid:{exc}")



def _source_position_and_graveyard_requirements(program):
    from ..rules.artifact_graveyard_departure import departure_artifact_return_runtime_dependencies
    from ..rules.artifact_graveyard_cast_activation import artifact_graveyard_cast_runtime_dependencies
    from ..rules.loot_transform_graveyard import loot_transform_runtime_dependencies
    from ..rules.loyalty_graveyard_cast import loyalty_graveyard_cast_runtime_dependencies
    from ..rules.owner_library_position_shapes import owner_library_position_runtime_dependencies
    from ..rules.searched_library_pairs import searched_pair_runtime_dependencies

    return (*owner_library_position_runtime_dependencies(program), *loot_transform_runtime_dependencies(program),
            *loyalty_graveyard_cast_runtime_dependencies(program),
            *artifact_graveyard_cast_runtime_dependencies(program),
            *departure_artifact_return_runtime_dependencies(program), *searched_pair_runtime_dependencies(program))



def _linked_zone_move_requirements(program, required, blockers):
    from ..rules.committed_nonland_mill import committed_mill_counter_program_dependencies
    from ..rules.linked_library_imprint import library_imprint_program_dependencies
    from ..rules.linked_color_cast_exile import linked_color_cast_exile_dependencies

    _library_search_runtime_requirements(program, required, blockers)
    try:
        from ..rules.linked_hand_mana import linked_hand_mana_program_dependencies
        from ..rules.linked_graveyard_mana import linked_graveyard_mana_program_dependencies

        required.update(library_imprint_program_dependencies(program))
        required.update(linked_hand_mana_program_dependencies(program))
        required.update(linked_graveyard_mana_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_library_imprint:invalid:{exc}")
    from ..rules.revealed_legendary_graveyard_treasure import revealed_legendary_treasure_program_dependencies

    try:
        required.update(revealed_legendary_treasure_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_revealed_legendary_treasure:invalid:{exc}")
    try:
        required.update(committed_mill_counter_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_committed_nonland_mill:invalid:{exc}")
    try:
        required.update(linked_color_cast_exile_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_color_cast_exile:invalid:{exc}")
    try:
        required.update(_source_position_and_graveyard_requirements(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_owner_library_position:invalid:{exc}")


def _zone_move_runtime_requirements(program, required, blockers):
    from ..rules.private_opponent_library_cast_shapes import private_opponent_library_cast_program_dependencies

    _linked_zone_move_requirements(program, required, blockers)

    from ..stack_counter import (
        announced_x_counter_runtime_dependencies, creature_count_counter_runtime_dependencies, no_mana_spent_counter_runtime_dependencies,
        other_controller_ability_counter_runtime_dependencies,
    )
    from ..public_zone_moves import stack_spell_exile_runtime_dependencies
    from ..rules.opponent_spell_or_permanent_return import opponent_spell_or_permanent_return_runtime_dependencies
    from ..rules.mill_capability_shapes import committed_mill_followup_program_dependencies
    from ..rules.milled_permanent_recovery import milled_permanent_recovery_runtime_dependencies
    from ..rules.destruction_regeneration_capability_shapes import kicked_entry_destruction_runtime_dependencies
    from ..rules.opponent_greatest_power_destruction import greatest_power_destruction_runtime_dependencies
    from ..rules.opponent_greatest_power_exile import greatest_power_exile_runtime_dependencies
    from ..rules.greatest_power_target_shapes import greatest_power_target_runtime_dependencies
    from ..rules.chosen_color_target_shapes import chosen_color_target_runtime_dependencies
    from ..rules.chosen_type_target_shapes import chosen_creature_type_target_runtime_dependencies
    from ..rules.temporary_self_copy import temporary_creature_copy_set_runtime_dependencies, temporary_self_copy_runtime_dependencies
    from ..rules.temporary_copy_subject import temporary_copy_subject_runtime_dependencies
    from ..rules.persistent_public_copy import controlled_artifact_copy_runtime_dependencies, persistent_public_copy_runtime_dependencies
    from ..rules.linked_exile_copy import linked_exile_copy_runtime_dependencies
    from ..rules.announced_artifact_copy import announced_artifact_copy_runtime_dependencies
    from ..rules.graveyard_exile_copy import graveyard_exile_copy_runtime_dependencies
    from ..rules.temporary_occurrence_copy import temporary_occurrence_copy_runtime_dependencies
    from ..rules.token_creation_capability_shapes import graveyard_target_token_copy_runtime_dependencies
    from ..rules.control_capability_shapes import (
        permanent_control_exchange_runtime_dependencies, two_controller_creature_transfer_runtime_dependencies,
    )
    from ..rules.library_bottom_results import library_bottom_result_runtime_dependencies
    from ..rules.reveal_until_reflexive import reveal_until_reflexive_runtime_dependencies
    from ..rules.recipient_control_shapes import recipient_control_runtime_dependencies
    from ..rules.owner_departure_tokens import owner_departure_token_runtime_dependencies
    from ..rules.owner_control_shapes import owner_control_runtime_dependencies
    from ..rules.aggregated_arrival_counters import matching_arrival_counter_program_dependencies
    from ..counter_placement_sets import (
        source_and_controlled_subtype_program_dependencies, source_power_combat_counter_program_dependencies,
    )
    from ..rules.controlled_counter_monstrosity import controlled_counter_monstrosity_runtime_dependencies
    from ..rules.event_counter_threshold import event_counter_threshold_program_dependencies
    from ..rules.graveyard_card_targets import (
        graveyard_copy_cast_runtime_dependencies,
        opposing_graveyard_return_runtime_dependencies, public_graveyard_return_runtime_dependencies,
        targeted_graveyard_cast_runtime_dependencies,
    )
    from ..rules.controlled_return import controlled_return_runtime_dependencies
    from ..rules.commander_return_choice import commander_return_runtime_dependencies
    from ..rules.void_counter_play import runtime_dependencies as void_play_dependencies
    from ..rules.silver_exile_loyalty import silver_loyalty_runtime_dependencies
    from ..rules.entry_return_capability_shapes import checked_entry_return_runtime_dependencies
    from ..rules.public_zone_move_capability_shapes import (
        chosen_color_permanent_return_runtime_dependencies, chosen_type_tap_state_runtime_dependencies,
        targeted_same_turn_graveyard_return_runtime_dependencies,
        triggered_source_exile_runtime_dependencies,
    )
    from ..rules.reanimation_capability_shapes import (
        event_battlefield_return_runtime_dependencies,
        source_power_reanimation_runtime_dependencies,
    )

    from ..rules.source_bound_delayed_return import source_bound_return_runtime_dependencies
    from ..rules.enchanted_departure_events import enchanted_departure_return_runtime_dependencies
    from ..rules.death_battlefield_return import death_battlefield_return_runtime_dependencies
    from ..rules.discarded_card_exile import discarded_card_exile_runtime_dependencies

    for resolver, label in (
        (discarded_card_exile_runtime_dependencies, "discarded_card_exile"),
        (private_opponent_library_cast_program_dependencies, 'private_opponent_library_cast'),
        (source_bound_return_runtime_dependencies, 'source_bound_delayed_return'),
        (target_death_return_runtime_dependencies, 'target_death_return_to_hand'),
        (death_battlefield_return_runtime_dependencies, 'death_battlefield_return'),
        (source_and_controlled_subtype_program_dependencies, 'source_and_controlled_subtype_counter'),
        (source_power_combat_counter_program_dependencies, 'source_power_combat_counter_set'),
        (controlled_counter_monstrosity_runtime_dependencies, 'controlled_counter_monstrosity'),
        (event_counter_threshold_program_dependencies, 'event_counter_threshold'),
        (matching_arrival_counter_program_dependencies, 'matching_arrival_counter'),
        (other_controller_ability_counter_runtime_dependencies, 'other_controller_ability_counter'),
        (no_mana_spent_counter_runtime_dependencies, 'no_mana_spent_counter'),
        (announced_x_counter_runtime_dependencies, 'announced_x_counter'),
        (creature_count_counter_runtime_dependencies, 'creature_count_counter'),
        (stack_spell_target_counter_runtime_dependencies, 'stack_spell_target_counter'),
        (paradigm_runtime_dependencies, 'paradigm'),
        (target_copy_recipient_runtime_dependencies, 'target_copy_recipient'),
        (spell_self_exile_runtime_dependencies, 'spell_self_exile'),
        (spell_self_shuffle_runtime_dependencies, 'spell_self_shuffle'),
        (spell_self_bottom_runtime_dependencies, 'spell_self_bottom'),
        (conditional_spell_return_runtime_dependencies, 'conditional_spell_return'),
        (selected_hand_reveal_discard_runtime_dependencies, 'selected_hand_reveal_discard'),
        (triggered_source_exile_runtime_dependencies, 'triggered_source_exile'),
        (chosen_color_permanent_return_runtime_dependencies, 'chosen_color_permanent_return'),
        (chosen_type_tap_state_runtime_dependencies, 'chosen_type_tap_state'),
        (targeted_same_turn_graveyard_return_runtime_dependencies, 'targeted_same_turn_graveyard_return'),
        (public_graveyard_return_runtime_dependencies, 'public_graveyard_return'),
        (opposing_graveyard_return_runtime_dependencies, 'opposing_graveyard_return'),
        (targeted_graveyard_cast_runtime_dependencies, 'targeted_graveyard_cast'),
        (graveyard_copy_cast_runtime_dependencies, 'graveyard_copy_cast'),
        (source_power_reanimation_runtime_dependencies, 'source_power_reanimation'),
        (checked_entry_return_runtime_dependencies, 'checked_entry_return'),
        (controlled_return_runtime_dependencies, 'controlled_return'),
        (commander_return_runtime_dependencies, 'commander_return'),
        (void_play_dependencies, 'void_counter_play'),
        (silver_loyalty_runtime_dependencies, 'silver_exile_loyalty'),
        (stack_spell_exile_runtime_dependencies, 'stack_spell_exile'),
        (opponent_spell_or_permanent_return_runtime_dependencies, 'opponent_spell_or_permanent_return'),
        (committed_mill_followup_program_dependencies, 'milled_creature_tokens'),
        (milled_permanent_recovery_runtime_dependencies, 'milled_permanent_recovery'),
        (kicked_entry_destruction_runtime_dependencies, 'kicked_entry_destruction'),
        (greatest_power_destruction_runtime_dependencies, 'opponent_greatest_power_destruction'),
        (greatest_power_exile_runtime_dependencies, 'opponent_greatest_power_exile'),
        (greatest_power_target_runtime_dependencies, 'greatest_power_creature_target'),
        (chosen_creature_type_target_runtime_dependencies, 'source_chosen_creature_type_target'),
        (chosen_color_target_runtime_dependencies, 'source_chosen_color_target'),
        (temporary_self_copy_runtime_dependencies, 'temporary_self_copy'),
        (linked_exile_copy_runtime_dependencies, 'linked_exile_self_copy'),
        (announced_artifact_copy_runtime_dependencies, 'announced_artifact_self_copy'),
        *_announced_zone_activation_checks(),
        (graveyard_exile_copy_runtime_dependencies, 'graveyard_exile_self_copy'),
        (temporary_creature_copy_set_runtime_dependencies, 'temporary_creature_copy_set'),
        (temporary_copy_subject_runtime_dependencies, 'temporary_copy_subject'),
        (persistent_public_copy_runtime_dependencies, 'persistent_public_copy'),
        (controlled_artifact_copy_runtime_dependencies, 'controlled_artifact_copy'),
        (temporary_occurrence_copy_runtime_dependencies, 'temporary_occurrence_copy'),
        (graveyard_target_token_copy_runtime_dependencies, 'graveyard_target_token_copy'),
        (recipient_control_runtime_dependencies,'recipient_control'),
        (owner_departure_token_runtime_dependencies, 'owner_departure_token'),
        (permanent_control_exchange_runtime_dependencies, 'permanent_control_exchange'),
        (two_controller_creature_transfer_runtime_dependencies, 'two_controller_creature_transfer'),
        (library_bottom_result_runtime_dependencies, 'library_bottom_creature_result'),
        (reveal_until_reflexive_runtime_dependencies, 'reveal_until_reflexive'),
        (owner_control_runtime_dependencies,'owner_control'),
        (event_battlefield_return_runtime_dependencies, 'attached_event_battlefield_return'),
        (enchanted_departure_return_runtime_dependencies, 'enchanted_departure_return'),
    ):
        try:
            required.update(resolver(program))
        except (ValueError, TypeError) as exc:
            blockers.add(f'runtime_{label}:invalid:{exc}')
