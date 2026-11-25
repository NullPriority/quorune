from __future__ import annotations

"""Existing typed result leaves bound to one normalized trigger occurrence."""

from . import regex_working_set as re

from ..rules.source_references import SourceReferenceSpec
from .target_announcement_bindings import target_announcement_result, target_announcement_bound_result
from .tap_state_event_bindings import tap_state_bound_result
from .scalar_effect_amounts import trigger_scalar_effect_amount_template
from .token_copy_templates import token_copy_recipe_template
from .fixed_source_combat_growth import fixed_source_combat_growth_effect_template
from .fixed_entry_return_requirements import (
    fixed_controller_return_choice_template,
    fixed_entry_return_effect_template,
)
from .endure_templates import binding_endure_trigger_result


_EXTRA_COMBAT_AFTER_DAMAGE = re.compile(
    r"Untap (?P<selection>it|each creature you control)\. "
    r"After this phase, there is an additional combat phase\.",
    re.IGNORECASE,
)


def _extra_combat_after_source_damage_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("damage.dealt.self", "source_combat_damage_player", "battlefield")
        or body != binding.body
    ):
        return None
    match = _EXTRA_COMBAT_AFTER_DAMAGE.fullmatch(body)
    if match is None:
        return None

    from ..extra_combat_model import ExtraCombatUntap, extra_combat_effect

    untap = (
        ExtraCombatUntap.SOURCE if match["selection"].casefold() == "it"
        else ExtraCombatUntap.CONTROLLED_CREATURES
    )
    return "fixed-source-damage-untap-extra-combat-v1", (extra_combat_effect(untap),), None, ()


def _equipment_source_excluding_destruction_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ('damage.dealt', 'equipped_creature_combat_damage_a_player', 'battlefield')
        or body != binding.body
        or body.casefold() != 'destroy all permanents other than this equipment.'
    ):
        return None
    from ..rules.qualified_destruction import (
        SOURCE_INCARNATION_EXCLUSION_FIELD, SOURCE_INCARNATION_EXCLUDING_SET,
    )

    effect = {
        'op': 'destroy_all',
        'source': '$source.zone_object',
        'set': SOURCE_INCARNATION_EXCLUDING_SET.to_dict(),
        SOURCE_INCARNATION_EXCLUSION_FIELD: 1,
    }
    return 'destroy-all-except-original-equipment-v1', (effect,), None, ('destroy', 'destroy-fixed-set')


def _bound_source_combat_result(binding, body, *, allow_optional):
    """Lower fixed results from an original source combat occurrence."""
    source_excluding = _equipment_source_excluding_destruction_result(binding, body)
    if source_excluding is not None:
        return source_excluding
    extra_combat = _extra_combat_after_source_damage_result(binding, body)
    if extra_combat is not None:
        return extra_combat
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        == ("creature.becomes_blocked", "this_creature_becomes_blocked", "battlefield")
        and body == binding.body
    ):
        if allow_optional:
            from .hand_inspection_templates import blocked_defender_hand_observation_result

            if observation := blocked_defender_hand_observation_result(body):
                return observation
        if re.fullmatch(r"Regenerate it[.]?", body, re.I):
            from .regeneration_templates import self_regeneration_effect_template

            return self_regeneration_effect_template("Regenerate this creature.").compiled()
        if re.fullmatch(r"Defending player discards a card at random[.]?", body, re.I):
            from ..random_discard import random_discard_effect_template

            result = random_discard_effect_template("Discard a card at random.")
            effect = {**result[1][0], "players": ["$context.defending_player"]}
            return "fixed-blocked-defender-random-discard-v1", (effect,), None, result[3]
    from .affected_player_sacrifice_templates import most_lands_attack_sacrifice_result
    from .event_player_choice_templates import attacked_defender_exile_result
    from .next_spell_cost_templates import recurring_attack_spell_cost_template
    from .temporary_cast_restrictions import attacked_defender_cast_restriction

    return (attacked_defender_cast_restriction(binding, body)
            or attacked_defender_exile_result(binding, body)
            or recurring_attack_spell_cost_template(binding, body)
            or most_lands_attack_sacrifice_result(binding, body))


def _equipped_life_and_optional_graveyard_return(binding, body, *, compile_effect):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("damage.dealt", "equipped_creature_combat_damage_a_player", "battlefield")
        or body != binding.body
        or body.casefold() != (
            "you gain 3 life and you may return up to one target creature card "
            "from your graveyard to your hand."
        )
    ):
        return None

    from ..rules.fixed_homogeneous_target_set_capability_shapes import (
        OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION,
    )

    template, effects, targets, mechanics = compile_effect(
        "You gain 3 life. You may return up to one target creature card from your graveyard to your hand."
    )
    if template is None:
        return None
    life, returning = effects
    return template, (
        life, {**returning, "schema_version": OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION},
    ), targets, mechanics


def binding_effect_template(binding, body: str, *, card_name: str, effect_template, _allow_payment: bool = True, source_counter_variants=(), _allow_optional: bool = True):
    if _allow_optional:
        equipped_return = _equipped_life_and_optional_graveyard_return(
            binding, body, compile_effect=lambda text: effect_template(text, card_name=card_name),
        )
        if equipped_return is not None:
            return equipped_return, False
    from .shared_type_library_templates import shared_type_library_result_template
    shared = shared_type_library_result_template(binding, body,
        compile_result=lambda clause: effect_template(clause, card_name=card_name))
    if shared is not None:
        return shared, False
    if _allow_payment:
        from .optional_sacrifice_stat_results import optional_sacrifice_stat_binding_result
        paid_stat = optional_sacrifice_stat_binding_result(binding, body, compile_fixed=lambda text: effect_template(text, card_name=card_name)) if _allow_optional else None
        if paid_stat is not None:return paid_stat
        from .life_gain_counted_results import life_gain_counted_counter_payment
        counted_payment = life_gain_counted_counter_payment(body, binding=binding, compile_result=lambda child: binding_effect_template(
            binding, child, card_name=card_name, effect_template=effect_template, _allow_payment=False,
            source_counter_variants=source_counter_variants, _allow_optional=_allow_optional))
        if counted_payment is not None:return counted_payment
    from .event_outcome_templates import fixed_event_outcome_template
    outcome = fixed_event_outcome_template(binding, body, card_name=card_name, effect_template=effect_template)
    if outcome is not None: return outcome
    from .event_damage_source_templates import event_damage_source_template
    dealer = event_damage_source_template(binding, body, card_name=card_name, effect_template=effect_template, allow_optional=_allow_optional)
    if dealer is not None:return dealer,False
    from .referenced_damage_templates import event_fixed_damage_template
    referenced=event_fixed_damage_template(binding,body,card_name=card_name)
    if referenced is not None:return referenced,False
    from .player_maximum_templates import player_maximum_control_result
    maximum=player_maximum_control_result(binding,body,card_name=card_name)
    if maximum is not None:return maximum,False
    from .recipient_control_templates import source_bound_recipient_control_result
    gift=source_bound_recipient_control_result(binding,body,card_name=card_name)
    if gift is not None:return gift,False
    from .event_counter_results import event_counter_threshold_result

    threshold = event_counter_threshold_result(binding, body, effect_template=effect_template, card_name=card_name)
    if threshold is not None:
        return threshold
    from .combat_prevention_source_results import bound_source_combat_prevention
    source_prevention=bound_source_combat_prevention(binding,body)
    if source_prevention is not None:return source_prevention,False
    from .discard_result_templates import event_player_discard_result_template

    if discard_result := event_player_discard_result_template(binding, body):
        return discard_result, False
    source_combat = _bound_source_combat_result(binding, body, allow_optional=_allow_optional)
    if source_combat is not None:
        return source_combat, False
    from .source_self_effect_templates import bound_source_fixed_damage
    source_damage = bound_source_fixed_damage(binding, body)
    if source_damage is not None:
        return source_damage, False
    if binding.variant in {"source_attacks", "your end step"} and body == binding.body:
        from .reanimation_templates import relative_target_reanimation_template
        event = "attack" if binding.variant == "source_attacks" else "end_step"
        returned = relative_target_reanimation_template(body, card_name=card_name, event=event)
        if returned is not None:
            return returned, False
    if binding.variant in {"your upkeep", "your end step"} and body == binding.body:
        return_choice = fixed_controller_return_choice_template(body)
        if return_choice is not None:
            return return_choice.compiled(), False
    if binding.variant == "source_attacks" and body == binding.body:
        from .reanimation_entry_counter_templates import conditional_reanimation_entry_counter_template
        conditional_return = conditional_reanimation_entry_counter_template(body, card_name=card_name)
        if conditional_return is not None:
            return conditional_return, False
        from .graveyard_copy_cast_templates import relative_graveyard_copy_template
        from .targeted_graveyard_cast_templates import relative_graveyard_cast_template
        relative = relative_graveyard_copy_template(body, card_name=card_name, event="attack") or relative_graveyard_cast_template(
            body, card_name=card_name,
        )
        if relative is not None:
            return relative, False
    if body == binding.body and body.casefold().startswith('you may return '):
        from .controlled_return_templates import controlled_return_template
        selected = controlled_return_template(body, source_is_permanent=binding.active_zone == 'battlefield')
        if selected is not None:
            return selected, False
    from .exile_templates import triggered_source_exile_template
    source_exile = triggered_source_exile_template(binding, body)
    if source_exile is not None:
        return source_exile, True
    from .discover_templates import cast_event_fixed_top_exile, damage_player_qualified_exile_cast
    exile_cast = damage_player_qualified_exile_cast(binding, body) or cast_event_fixed_top_exile(binding, body)
    if exile_cast is not None:
        return exile_cast, False
    if _allow_payment and _allow_optional and body == binding.body:
        from .occurrence_object_results import entry_equipment_attachment_result, entry_permanent_result
        entry = entry_permanent_result(binding, body) or entry_equipment_attachment_result(
            binding, body, card_name=card_name, effect_template=effect_template,
        )
        if entry is not None:
            return entry
    if binding.active_zone == "battlefield":
        from .self_transform_templates import fixed_self_transform_effect_template
        transformation = fixed_self_transform_effect_template(body, card_name=card_name, triggered=True)
        if transformation is not None:
            return transformation, False
        named_explore = re.fullmatch(r"(?P<source>.+?) explores\.?", body, re.IGNORECASE)
        if named_explore is not None and SourceReferenceSpec(card_name).matches(named_explore["source"]):
            from .explore_templates import single_explore_effect_template

            return single_explore_effect_template("This permanent explores.").compiled(), False
    if _allow_payment:
        from .source_sacrifice_payments import normalized_source_sacrifice_payment_text
        body=normalized_source_sacrifice_payment_text(binding,body,card_name=card_name)
    endure = binding_endure_trigger_result(binding, body, source_name=card_name, effect_template=effect_template)
    if endure is not None:
        return endure, False
    from ..source_sacrifice_model import (
        SOURCE_SACRIFICE_MECHANIC, SOURCE_UNATTACHMENT_VARIANT, former_attachment_sacrifice_effect,
    )
    if binding.variant == SOURCE_UNATTACHMENT_VARIANT and body.strip().casefold() == "sacrifice that permanent.":
        return ("fixed-former-attachment-sacrifice-v1", (former_attachment_sacrifice_effect(),),
            None, (SOURCE_SACRIFICE_MECHANIC,)), False
    from .event_player_modifiers import event_player_modifier_result
    modifier=event_player_modifier_result(binding,body)
    if modifier is not None:return modifier
    from .per_player_query_life import source_attack_opponent_subtype_absence_life_result
    absence = source_attack_opponent_subtype_absence_life_result(binding, body)
    if absence is not None:
        return absence, False
    from .event_player_target_results import event_player_target_result
    defending_target=event_player_target_result(binding,body,compile_result=lambda text: binding_effect_template(
        binding,text,card_name=card_name,effect_template=effect_template,_allow_payment=_allow_payment,
        source_counter_variants=source_counter_variants,_allow_optional=_allow_optional))
    if defending_target is not None:return defending_target
    if _allow_optional:
        from .combat_removal_templates import optional_blocked_combat_removal_template
        combat_removal = optional_blocked_combat_removal_template(binding, body)
        if combat_removal is not None:
            return combat_removal, True
    compiled, current = _binding_result_template(binding,body,card_name=card_name,effect_template=effect_template,
        _allow_payment=_allow_payment,source_counter_variants=source_counter_variants,_allow_optional=_allow_optional)
    if binding.event.value == "spell.cast" and compiled[1] == ({"op": "bounce", "card": "$source"},):
        from .source_self_effect_templates import pin_source_result_incarnation

        compiled = pin_source_result_incarnation(compiled)
    result = compiled, current
    if result[0][0] is not None or not _allow_payment:
        return result
    from .reflexive_payment_templates import binding_aware_reflexive_payment_template

    return binding_aware_reflexive_payment_template(
        body, compile_result=lambda child: binding_effect_template(
            binding, child, card_name=card_name, effect_template=effect_template,
            source_counter_variants=source_counter_variants, _allow_payment=False,
        ),
    ) or result


def _temporary_occurrence_copy_result(binding, body):
    from ..rules.temporary_occurrence_copy import (
        TEMPORARY_OCCURRENCE_COPY_MECHANIC, temporary_occurrence_copy_condition_matches,
        temporary_occurrence_copy_effect,
    )

    if (binding.event.value != "creature.enter" or binding.active_zone != "battlefield"
            or body != binding.body or not temporary_occurrence_copy_condition_matches(binding.event_condition)
            or " ".join(body.split()).casefold() !=
            "you may have this creature become a copy of that creature until end of turn."):
        return None
    return ("temporary-occurrence-copy-v1", (temporary_occurrence_copy_effect(),), None,
            (TEMPORARY_OCCURRENCE_COPY_MECHANIC,)), False


def _source_bound_optional_trigger_result(binding, body):
    from .optional_effect_templates import optional_shared_source_type_trigger_result

    return _temporary_occurrence_copy_result(binding, body) or optional_shared_source_type_trigger_result(binding, body)


def _bound_source_specific_result(binding, body, *, card_name, effect_template):
    from .source_block_defender import source_block_defender_result

    if block_change := source_block_defender_result(binding, body):
        return block_change
    from .attack_counter_unblockable import attack_counter_unblockable_result
    from .source_optional_creature_unblockable import clue_sacrifice_source_optional_unblockable
    from .source_counter_reflexive import source_counter_reflexive_result
    from .third_resolved_source_trigger import third_resolved_source_trigger_result

    from .affected_player_sacrifice_templates import attack_sacrifice_stat_result, entry_sacrifice_power_draw_result
    from .fixed_public_multi_event_bindings import graveyard_creature_transition_result
    from .fixed_public_event_trigger_bindings import linked_player_cast_mill_result
    from .received_damage_bindings import linked_player_receiving_result

    if result := (graveyard_creature_transition_result(binding, body)
                  or linked_player_receiving_result(binding, body) or linked_player_cast_mill_result(binding, body)):
        return result
    if sacrifice := (entry_sacrifice_power_draw_result(binding, body) or attack_sacrifice_stat_result(binding, body)):
        return sacrifice
    result = (clue_sacrifice_source_optional_unblockable(binding, body, card_name=card_name)
              or source_counter_reflexive_result(binding, body, card_name=card_name)
              or attack_counter_unblockable_result(binding, body, card_name=card_name)
              or third_resolved_source_trigger_result(binding, body, card_name=card_name))
    if result is not None:
        return result, False
    from .graveyard_self_return import graveyard_self_return_result
    returned=graveyard_self_return_result(binding,body,card_name=card_name)
    if returned is not None:return returned
    from .delayed_object_templates import delayed_object_result
    delayed=delayed_object_result(binding,body,card_name=card_name)
    if delayed is not None:return delayed
    from .source_counter_quantity_sequences import source_bound_sequence_result
    source_sequence=source_bound_sequence_result(binding,body,card_name=card_name,effect_template=effect_template)
    if source_sequence is not None:return source_sequence
    return None


def _binding_result_template(binding, body: str, *, card_name: str, effect_template, _allow_payment: bool = True, source_counter_variants=(), _allow_optional: bool = True):
    source_result = _bound_source_specific_result(binding, body, card_name=card_name, effect_template=effect_template)
    if source_result is not None:
        return source_result
    from .source_self_effect_templates import source_cycled_optional_damage_result
    cycled_damage = source_cycled_optional_damage_result(binding, body) if _allow_optional and _allow_payment else None
    if cycled_damage is not None:
        return cycled_damage, False
    from .counter_removal_templates import fixed_source_counter_removal_effect_template, all_named_source_counter_removal_effect_template
    source_pronoun = binding.event.value.endswith('.self') or binding.variant in {
            'source_attacks', 'this_creature_blocks', 'this_creature_becomes_blocked',
            'source_dealt_damage', 'source_received_combat_damage',
        }
    if binding.active_zone == 'battlefield' and _allow_optional and _allow_payment:
        for compile_removal in (fixed_source_counter_removal_effect_template, all_named_source_counter_removal_effect_template):
            source_removal = compile_removal(body, card_name=card_name, allow_source_pronoun=source_pronoun)
            if source_removal is not None:
                return source_removal, False
    if _allow_optional:
        from .binding_optional_results import binding_aware_optional_result
        optional = _source_bound_optional_trigger_result(binding, body) or binding_aware_optional_result(body, compile_child=lambda text: binding_effect_template(binding,text,card_name=card_name,effect_template=effect_template,_allow_payment=_allow_payment,source_counter_variants=source_counter_variants,_allow_optional=False))
        if optional is not None:return optional
    from ..expend_model import EXPEND_CAPABILITY
    if EXPEND_CAPABILITY in binding.explicit_capabilities:
        from .source_self_effect_templates import normalized_source_result_body
        normalized=normalized_source_result_body(body)
        if normalized is not None:body=normalized
    from .fixed_stack_spell_copy import cast_event_spell_copy_template
    cast_copy=cast_event_spell_copy_template(binding,body)
    if cast_copy is not None:return cast_copy
    from .attack_event_results import qualified_attack_characteristic_result
    attack_result=qualified_attack_characteristic_result(binding,body,effect_template=effect_template,card_name=card_name)
    if attack_result is not None:return attack_result
    from .occurrence_object_results import occurrence_object_result, occurrence_power_damage_result
    occurrence=occurrence_power_damage_result(binding,body) or occurrence_object_result(binding,body,effect_template=effect_template,card_name=card_name)
    if occurrence is not None:return occurrence
    from .event_counter_results import public_event_counter_set_result
    counter_set=public_event_counter_set_result(binding,body,effect_template=effect_template,card_name=card_name,accepted_variants=source_counter_variants)
    if counter_set is not None:return counter_set
    from .event_counter_results import public_event_source_counter_result
    source_counter = public_event_source_counter_result(
        binding, body, effect_template=effect_template, card_name=card_name,
        accepted_variants=source_counter_variants,
        allow_source_pronoun=source_pronoun or binding.variant == 'source_attacks_or_blocks',
    )
    if source_counter is not None:return source_counter
    from .event_counter_results import public_event_target_counter_result
    target_counter = public_event_target_counter_result(
        binding, body, effect_template=effect_template, card_name=card_name,
        accepted_variants=source_counter_variants,
    )
    if target_counter is not None:return target_counter
    from .event_counter_results import entry_event_counter_result
    counter=entry_event_counter_result(binding,body,effect_template=effect_template,card_name=card_name)
    if counter is not None:return counter
    from .event_player_results import fixed_event_player_result
    player_result=fixed_event_player_result(binding,body,effect_template=effect_template,card_name=card_name)
    if player_result is not None:return player_result
    from .event_player_results import fixed_event_player_damage_result
    player_damage=fixed_event_player_damage_result(binding,body,effect_template=effect_template,card_name=card_name)
    if player_damage is not None:return player_damage
    from .scheduled_turn_skip_templates import damage_player_turn_skip_result
    skip = damage_player_turn_skip_result(binding, body)
    if skip is not None:
        return skip
    from .event_player_choice_templates import fixed_event_player_choice_result
    player_choice = fixed_event_player_choice_result(binding, body)
    if player_choice is not None:return player_choice
    from .mill_templates import event_recipient_mill_result
    mill=event_recipient_mill_result(binding,body,compile_fixed=lambda text:effect_template(text,card_name=card_name),source_name=card_name)
    if mill is not None:return mill
    if source_pronoun or binding.variant in {'source_turned_face_up', 'source_enters_or_attacks'}:
        from .explore_templates import single_explore_effect_template
        explored = single_explore_effect_template(body, allow_source_pronoun=True)
        if explored is not None:return explored.compiled(), False
        from .damage_templates import source_pronoun_damage_effect_template
        damage=source_pronoun_damage_effect_template(body)
        if damage is not None:return damage.compiled(),False
        from .connive_templates import fixed_connive_effect_template
        connive = fixed_connive_effect_template(body, source_name=card_name, source_is_permanent=True,
                                                allow_source_pronoun=True)
        if connive is not None:return connive, False
    if _allow_payment:
        from .optional_payment_templates import (
            binding_aware_mandatory_source_counter_followup, binding_aware_optional_mana_payment_template,
        )
        def compile_payment_result(text):
            return binding_effect_template(
                binding, text, card_name=card_name, effect_template=effect_template,
                _allow_payment=False, source_counter_variants=source_counter_variants,
            )
        required = binding_aware_mandatory_source_counter_followup(binding, body, compile_result=compile_payment_result)
        if required is not None:
            return required
        payment = binding_aware_optional_mana_payment_template(
            body,
            source_is_permanent=binding.active_zone == "battlefield",
            compile_result=compile_payment_result,
        )
        if payment is not None:
            return payment
    from .affected_player_discard_templates import damage_player_discard_result,defending_player_discard_result
    defending_discard=defending_player_discard_result(binding,body)
    if defending_discard is not None:return defending_discard
    discard = damage_player_discard_result(binding, body)
    if discard is not None:
        return discard
    from ..random_discard import damage_player_random_discard_result
    random_discard = damage_player_random_discard_result(binding, body)
    if random_discard is not None:
        return random_discard
    specialized = fixed_source_combat_growth_effect_template(body, event=binding.event.value, variant=binding.variant)
    if specialized[0] is not None:return specialized,True
    if binding.event.value.endswith('.self') or binding.variant in {'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_public_tap_state_transition','source_dealt_damage','source_turned_face_up'}:
        from .source_self_effect_templates import normalized_source_result_body, pin_source_result_incarnation, _source_self_counter_payment
        payment=_source_self_counter_payment(body,card_name=card_name,compile_effect=effect_template)
        if payment is not None:return payment,True
        normalized=normalized_source_result_body(body)
        if normalized is not None:
            value=effect_template(normalized,card_name=card_name)
            if value[0] is not None:return pin_source_result_incarnation(value),True
    from .scheduled_player_triggers import scheduled_player_result
    scheduled=scheduled_player_result(binding,body,effect_template=effect_template,card_name=card_name)
    if scheduled is not None:return scheduled
    from .event_card_return_templates import event_card_return_template
    returned = event_card_return_template(binding, body, card_name=card_name, effect_template=effect_template)
    if returned is not None:
        return returned, False
    sacrifice = target_announcement_bound_result(binding, body, card_name=card_name)
    if sacrifice is not None:
        return sacrifice, False
    body = target_announcement_result(binding, body, card_name=card_name)
    event_result = tap_state_bound_result(binding, body, effect_template=effect_template, card_name=card_name)
    if event_result is not None:
        return event_result
    scalar = trigger_scalar_effect_amount_template(
        body, source_name=card_name, binding=binding,
        compile_fixed=lambda text: effect_template(text, card_name=card_name),
    )
    if scalar is not None:
        return scalar, False
    copy_recipe = token_copy_recipe_template(body, source_name=card_name, source_is_permanent=True,
                                             event=binding.event.value, event_variant=binding.variant)
    if copy_recipe is not None:
        return copy_recipe.compiled(), False
    if binding.variant == 'fixed_entry_return_requirement':
        entry_return = fixed_entry_return_effect_template(body)
        if entry_return[0] is not None:
            return entry_return, True
    return effect_template(body, card_name=card_name), False
