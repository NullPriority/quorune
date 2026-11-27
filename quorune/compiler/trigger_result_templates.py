from __future__ import annotations

"""Existing typed result leaves bound to one normalized trigger occurrence."""

from dataclasses import replace

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


def _cast_coin_result(binding, body, *, card_name):
    from ..replacement.immutable import FrozenMap
    from ..rules.cast_coin_transform import MECHANIC, TEMPLATE, coin_instruction, original_cast_condition

    if ((binding.event.value, binding.active_zone) != ("spell.cast", "battlefield")
            or binding.event_condition is None
            or FrozenMap(binding.event_condition) != FrozenMap(original_cast_condition())):
        return None
    match = re.fullmatch(
        r"flip a coin\. If you lose the flip, (?P<dealer>.+?) deals 1 damage to you\. "
        r"If you win the flip, you may exile (?P<source>.+?)\. If you do, return (?:it|him|her) "
        r"to the battlefield transformed under (?:its|his|her) owner['’]s control\.",
        body, re.IGNORECASE,
    )
    reference = SourceReferenceSpec(card_name)
    if match is None or not all(reference.matches(match[role]) for role in ("dealer", "source")):
        return None
    return TEMPLATE, (coin_instruction(),), None, (MECHANIC,)


def bound_trigger_effect_template(binding, body, *, card_name, effect_template, source_counter_variants):
    if coin := _cast_coin_result(binding, body, card_name=card_name):
        return coin, True
    from .trigger_event_vocabulary import FixedCounterTriggerEvent

    if binding.event is FixedCounterTriggerEvent.SOURCE_VEHICLE_ENTER and body == binding.body:
        from .per_player_target_sets import controlled_destruction_source_counter_template

        if result := controlled_destruction_source_counter_template(body):
            return result, False
    if binding.event is FixedCounterTriggerEvent.NONMANA_ACTIVATED:
        from .nonmana_activation_copy import nonmana_activation_copy_result

        return nonmana_activation_copy_result(binding, body), False
    if binding.event is FixedCounterTriggerEvent.NINJUTSU_ACTIVATED:
        from .fixed_public_action_event_bindings import fixed_ninjutsu_observer_effect

        return fixed_ninjutsu_observer_effect(effect_template(body, card_name=card_name)), True
    if (binding.event is FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST
            and binding.variant == "noncreature" and body == binding.body):
        counter_and_damage = re.fullmatch(
            r"put a \+1/\+1 counter on (?P<source>.+?) and it deals 1 damage to each opponent\.",
            body, re.IGNORECASE,
        )
        if counter_and_damage and SourceReferenceSpec(card_name).matches(counter_and_damage["source"]):
            body = f"Put a +1/+1 counter on {card_name}. {card_name} deals 1 damage to each opponent."
            binding = replace(binding, body=body)
        else:
            pump_and_damage = re.fullmatch(
                r"(?P<source>.+?) gets (?P<stats>[+-][0-9]+/[+-][0-9]+) until end of turn "
                r"and deals (?P<damage>[1-9][0-9]*) damage to each opponent\.",
                body,
                re.IGNORECASE,
            )
            if pump_and_damage and SourceReferenceSpec(card_name).matches(pump_and_damage["source"]):
                body = (f"This permanent gets {pump_and_damage['stats']} until end of turn. "
                        f"{card_name} deals {pump_and_damage['damage']} damage to each opponent.")
                binding = replace(binding, body=body)

    if (binding.event is FixedCounterTriggerEvent.SOURCE_ATTACKS
            and binding.variant == "source_attacks" and body == binding.body):
        from .target_recipient_results import controlled_artifact_draw_template

        result = controlled_artifact_draw_template(body, compile_component=lambda text: effect_template(text, card_name=card_name))
        if result is not None:
            return result.compiled(), False

    from .opponent_library_shuffle_templates import opponent_library_shuffle_result

    if shuffle_result := opponent_library_shuffle_result(binding, body):
        return shuffle_result, False
    from .cast_trigger_life_payment_templates import binding_with_cast_life_payment

    return binding_with_cast_life_payment(
        binding, body, card_name=card_name, effect_template=effect_template,
        source_counter_variants=source_counter_variants,
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


def _equipped_counter_proliferate_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("damage.dealt", "equipped_creature_combat_damage_a_player", "battlefield")
        or body != binding.body
        or body.casefold() != "put a +1/+1 counter on a creature you control, then proliferate."
    ):
        return None

    from ..counter_placement_targets import controlled_creature_counter_choice_effect
    from ..rules.fixed_effect_clause_shapes import FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
    from .proliferate_templates import ProliferateEffectTemplate

    proliferate = ProliferateEffectTemplate()
    return ("equipped-counter-then-proliferate-v1",
            (controlled_creature_counter_choice_effect(), *proliferate.effects), None,
            ("cr-122-counters", *proliferate.mechanics, FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC))


def _bound_source_combat_result(binding, body, *, allow_optional):
    """Lower fixed results from an original source combat occurrence."""
    from ..combat_damage_library_exile import combat_damage_cast_exile_effect, combat_damage_exile_effect
    from ..replacement.immutable import FrozenMap
    from ..rules.combat_damage_library_access import COMBAT_DAMAGE_LIBRARY_CONDITION
    from .exiled_target_library_search_templates import equipped_exiled_target_search_result
    from .graveyard_return_choice_templates import combat_surveil_land_return_template
    from .impulse_access_templates import IMPULSE_ACCESS_MECHANIC_ID
    from .public_tap_state_set_templates import combat_recipient_land_tap_untap_result

    if surveil_return := combat_surveil_land_return_template(binding, body):
        return surveil_return
    if ((binding.event.value, binding.variant, binding.active_zone)
            == ("damage.dealt.self", "source_combat_damage_player", "battlefield")
            and body == binding.body and FrozenMap(binding.event_condition or {}) == COMBAT_DAMAGE_LIBRARY_CONDITION
            and " ".join(body.split()).casefold() == ("that player exiles the top two cards of their library. "
                "until end of turn, you may play those cards without paying their mana costs.")):
        return ("combat-damage-recipient-top-exile-free-play-v1", (combat_damage_exile_effect(),),
                None, (IMPULSE_ACCESS_MECHANIC_ID,))
    if ((binding.event.value, binding.variant, binding.active_zone)
            == ("damage.dealt.self", "source_combat_damage_player", "battlefield")
            and body == binding.body and FrozenMap(binding.event_condition or {}) == COMBAT_DAMAGE_LIBRARY_CONDITION
            and " ".join(body.split()).casefold() == ("create a treasure token and exile the top card "
                "of that player's library. until end of turn, you may cast that card.")):
        from .token_templates import fixed_token_creation_effect_template

        treasure = fixed_token_creation_effect_template("Create a Treasure token.")
        return ("combat-damage-recipient-treasure-and-top-cast-v1", (treasure.effect, combat_damage_cast_exile_effect()),
                None, (*treasure.mechanics, IMPULSE_ACCESS_MECHANIC_ID))
    if exiled_search := equipped_exiled_target_search_result(binding, body):
        return exiled_search
    if land_set := combat_recipient_land_tap_untap_result(binding, body):
        return land_set
    if counter_proliferate := _equipped_counter_proliferate_result(binding, body):
        return counter_proliferate
    if allow_optional:
        from .optional_effect_templates import optional_entry_exempt_discard_trigger_result
        from .self_transform_templates import optional_equipped_source_transform_result

        if entry_exempt := optional_entry_exempt_discard_trigger_result(binding, body):
            return entry_exempt
        transform = optional_equipped_source_transform_result(binding, body)
        if transform is not None:
            return transform
    source_excluding = _equipment_source_excluding_destruction_result(binding, body)
    if source_excluding is not None:
        return source_excluding
    from .die_roll_draw_templates import combat_die_draw_hand_size_result, equipped_attack_die_result

    if die_draw := (combat_die_draw_hand_size_result(binding, body) or equipped_attack_die_result(binding, body)):
        return die_draw
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


def _bound_payment_result(binding, body, *, card_name, effect_template, source_counter_variants, allow_optional):
    if allow_optional:
        from .attached_copy_payment import attached_copy_payment_result

        if attached_copy := attached_copy_payment_result(binding, body, card_name=card_name):
            return attached_copy
        from .draw_step_drawing_settlement import draw_step_settlement_trigger_result

        if settlement := draw_step_settlement_trigger_result(binding, body):
            return settlement, False
        from .bounded_hand_cast_templates import (
            damaged_player_hand_cast_trigger_result, legendary_wizard_hand_cast_trigger_result,
        )

        hand_cast = (damaged_player_hand_cast_trigger_result(binding, body)
                     or legendary_wizard_hand_cast_trigger_result(binding, body))
        if hand_cast is not None:
            return hand_cast
    if (binding.event.value == "creature.dies" and binding.variant == "equipped_creature_dies"
            and binding.active_zone == "battlefield" and body == binding.body
            and body.casefold() == "you may pay x life, where x is its power. if you do, draw x cards."):
        from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
        from ..fixed_effect_payment import equipped_death_life_draw_effect

        return ("equipped-death-life-draw-v1", (equipped_death_life_draw_effect(),), None,
                (FIXED_EFFECT_PAYMENT_MECHANIC,)), True
    from .optional_sacrifice_stat_results import optional_sacrifice_stat_binding_result
    paid_stat = optional_sacrifice_stat_binding_result(binding, body, compile_fixed=lambda text: effect_template(text, card_name=card_name)) if allow_optional else None
    if paid_stat is not None:return paid_stat
    from .life_gain_counted_results import capped_life_gain_draw_payment, life_gain_counted_counter_payment

    if allow_optional and (draw_payment := capped_life_gain_draw_payment(body, binding=binding)):
        return draw_payment
    counted_payment = life_gain_counted_counter_payment(body, binding=binding, compile_result=lambda child: binding_effect_template(
        binding, child, card_name=card_name, effect_template=effect_template, _allow_payment=False,
        source_counter_variants=source_counter_variants, _allow_optional=allow_optional))
    if counted_payment is not None:return counted_payment


def _point_combat_result(binding, body):
    from ..rules.combat_point_counters import (
        INBOUND_POINT_VARIANT, OUTBOUND_POINT_VARIANT, point_combat_effects,
    )

    if binding.variant not in {INBOUND_POINT_VARIANT, OUTBOUND_POINT_VARIANT} or body != binding.body:
        return None
    outbound = binding.variant == OUTBOUND_POINT_VARIANT
    mechanics = ("cr-122-counters", "fixed-resolution-public-condition") if outbound else ("cr-122-counters",)
    return "fixed-combat-point-result-v1", point_combat_effects(outbound=outbound), None, mechanics


def _closed_event_outcome(binding, body, *, card_name, effect_template):
    from ..rules.attack_event_results import (ATTACHED_BLOCK_GROWTH_VARIANT, ATTACHED_BLOCK_GROWTH_TEMPLATE,
        attached_block_growth_effects)
    if binding.variant == ATTACHED_BLOCK_GROWTH_VARIANT:
        growth = re.fullmatch(
            r"it gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) until end of turn and you gain (?P<life>[1-9][0-9]*) life[.]?",
            body.strip(), re.I,
        )
        if growth is not None:
            try:
                effects = attached_block_growth_effects(*(int(growth[key]) for key in ("power", "toughness", "life")))
            except ValueError:
                return (None, (), None, ()), False
            return (ATTACHED_BLOCK_GROWTH_TEMPLATE, effects, None,
                    ("cr-611-continuous-effects", "cr-119-life")), False
    point_result = _point_combat_result(binding, body)
    if point_result is not None:
        return point_result, False
    from .event_outcome_templates import fixed_event_outcome_template

    return fixed_event_outcome_template(binding, body, card_name=card_name, effect_template=effect_template)


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
        paid = _bound_payment_result(
            binding, body, card_name=card_name, effect_template=effect_template,
            source_counter_variants=source_counter_variants, allow_optional=_allow_optional,
        )
        if paid is not None:
            return paid
    outcome = _closed_event_outcome(binding, body, card_name=card_name, effect_template=effect_template)
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
        from .controlled_return_templates import controlled_return_trigger_template
        selected = controlled_return_trigger_template(binding, body)
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
        from .occurrence_object_results import entry_aura_attachment_result, entry_equipment_attachment_result, entry_permanent_result

        entry = (
            entry_permanent_result(binding, body)
            or entry_aura_attachment_result(binding, body)
            or entry_equipment_attachment_result(binding, body, card_name=card_name, effect_template=effect_template)
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
    from ..permanent_event_state_model import COUNTERED_DAMAGE_SOURCE_VARIANT

    if binding.variant == COUNTERED_DAMAGE_SOURCE_VARIANT and body == binding.body:
        from .draw_templates import fixed_draw_effect_template

        return fixed_draw_effect_template(body), False

    from .optional_effect_templates import (
        optional_shared_entrant_type_mana_trigger_result, optional_shared_source_type_trigger_result,
        optional_targeted_control_trigger_result,
    )

    return (_temporary_occurrence_copy_result(binding, body)
            or optional_shared_entrant_type_mana_trigger_result(binding, body)
            or optional_shared_source_type_trigger_result(binding, body)
            or optional_targeted_control_trigger_result(binding, body))


def _equipped_color_upkeep_counter(binding, body, *, compile_effect):
    if (
        (binding.event.value, binding.variant, binding.active_zone) != ("step.begin", "your upkeep", "battlefield")
        or body != binding.body
    ):
        return None
    match = re.fullmatch(
        r"Put a \+1/\+1 counter on equipped creature if it['’]s (?P<color>white|blue|black|red|green)\.",
        body, re.I,
    )
    if match is None:
        return None

    from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec

    template, effects, schema, mechanics = compile_effect("Put a +1/+1 counter on equipped creature.")
    if template is None or len(effects) != 1 or schema is not None:
        return None
    effect = effects[0]
    colors = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
    reference = AttachmentReferenceSpec(
        AttachmentReferenceKind.EQUIPPED, "creature", schema_version=3,
        required_color=colors[match["color"].casefold()],
    )
    return (template, ({**effect, "card": reference.to_dict()},), schema, mechanics), False


def _upkeep_opponent_threshold_search_result(binding, body):
    from ..continuous_conditions import FixedPublicStateFact
    from ..source_sacrifice_model import SOURCE_SACRIFICE_MECHANIC, fixed_source_sacrifice_effect
    from .public_trigger_conditions import opponent_threshold_search_body

    if ((binding.event.value, binding.variant, binding.active_zone) != ("step.begin", "your upkeep", "battlefield")
            or getattr(binding.public_state_condition, "fact", None) is not FixedPublicStateFact.OPPONENT_CREATURE_COUNT_MAXIMUM
            or body != binding.body):
        return None
    search = opponent_threshold_search_body(body)
    if search is None:
        return None
    _, children, target_schema, mechanics = search
    return ("upkeep-source-sacrifice-and-creature-search-v1", (fixed_source_sacrifice_effect(), *children),
            target_schema, (SOURCE_SACRIFICE_MECHANIC, *mechanics))


def defending_player_top_exile_result(binding, body):
    from ..defending_player_library_exile import defending_player_exile_effect
    from ..replacement.immutable import FrozenMap
    from ..rules.defending_player_top_exile import DEFENDING_PLAYER_TOP_ATTACK_CONDITION
    from .impulse_access_templates import IMPULSE_ACCESS_MECHANIC_ID

    if ((binding.event.value, binding.variant, binding.active_zone) != ("creature.attacks", "source_attacks", "battlefield")
            or body != binding.body
            or FrozenMap(binding.event_condition or {}) != DEFENDING_PLAYER_TOP_ATTACK_CONDITION
            or " ".join(body.split()).casefold() != "defending player exiles the top twenty cards of their library."):
        return None
    return ("defending-player-twenty-top-exile-v1", (defending_player_exile_effect(),),
            None, (IMPULSE_ACCESS_MECHANIC_ID,))


def monarch_counter_distribution_result(binding, body):
    from ..replacement.immutable import FrozenMap
    from ..rules.cumulative_object_upkeep import CONTROLLER_UPKEEP
    from ..rules.monarch_counter_distribution import (
        MONARCH_COUNTER_DISTRIBUTION_MECHANICS, monarch_counter_distribution_effects,
        monarch_counter_distribution_target_schema,
    )

    if ((binding.event.value, binding.active_zone) != ("step.begin", "battlefield")
            or body != binding.body or FrozenMap(binding.event_condition or {}) != FrozenMap(CONTROLLER_UPKEEP)):
        return None
    material = " ".join(body.split()).casefold().replace("’", "'")
    if material != ("distribute two +1/+1 counters among up to two target creatures. then if you're the monarch, "
                    "double the number of +1/+1 counters on each creature you control."):
        return None
    return ("monarch-counter-distribution-doubling-v1", monarch_counter_distribution_effects(),
            monarch_counter_distribution_target_schema(), MONARCH_COUNTER_DISTRIBUTION_MECHANICS)


def monarch_knight_entry_damage_result(binding, body, *, card_name):
    from ..replacement.immutable import FrozenMap
    from ..rules.monarch_knight_entry_damage import (
        MONARCH_KNIGHT_ENTRY_DAMAGE_MECHANICS, guard_monarch_knight_damage, knight_damage_instruction,
    )
    from ..rules.monarch_movement_replacements import MONARCH_MOVEMENT_UPKEEP
    from .damage_templates import fixed_damage_effect_template
    from .declared_effect_amounts import declared_effect_amount_template
    from .token_templates import fixed_token_creation_effect_template

    if ((binding.event.value, binding.active_zone) != ("step.begin", "battlefield")
            or body != binding.body or FrozenMap(binding.event_condition or {}) != MONARCH_MOVEMENT_UPKEEP):
        return None
    token_text = "Create a 3/1 red Knight creature token."
    damage_text = "This enchantment deals X damage to each opponent, where X is the number of creatures you control."
    material = " ".join(body.split()).casefold().replace("’", "'")
    if material != f"{token_text} Then if you're the monarch, {damage_text}".casefold():
        return None

    def fixed_opponent_damage(text):
        fixed = fixed_damage_effect_template(text, card_name=card_name)
        if fixed is None:
            return None, (), None, ()
        template, effects, target_schema, mechanics = fixed.compiled()
        if (len(effects) != 1 or target_schema is not None or effects[0] !=
                {"op": "damage_each_opponent", "source": "$source", "amount": effects[0].get("amount")}):
            return None, (), None, ()
        return template, (knight_damage_instruction(effects[0]["amount"]),), None, mechanics

    token = fixed_token_creation_effect_template(token_text)
    damage = declared_effect_amount_template(damage_text, source_name=card_name, compile_fixed=fixed_opponent_damage)
    if token is None or damage is None:
        return None
    return ("monarch-knight-entry-damage-v1", (*token.compiled()[1], guard_monarch_knight_damage(damage[1][0])),
            None, MONARCH_KNIGHT_ENTRY_DAMAGE_MECHANICS)


def monarch_selected_player_mill_result(binding, body):
    from ..replacement.immutable import FrozenMap
    from ..rules.monarch_movement_replacements import MONARCH_MOVEMENT_UPKEEP
    from ..rules.monarch_selected_player_mill import (
        monarch_selected_player_mill_effect, monarch_selected_player_mill_schema,
    )

    if ((binding.event.value, binding.active_zone) != ("step.begin", "battlefield")
            or body != binding.body or FrozenMap(binding.event_condition or {}) != MONARCH_MOVEMENT_UPKEEP):
        return None
    material = " ".join(body.split()).casefold().replace("’", "'")
    if material != ("any number of target players each mill two cards. "
                    "if you're the monarch, each of those players mills ten cards instead."):
        return None
    return ("monarch-selected-player-mill-v1", (monarch_selected_player_mill_effect(),),
            monarch_selected_player_mill_schema(), ("fixed-resolution-public-condition",))


def monarch_movement_replacement_result(binding, body):
    from ..replacement.immutable import FrozenMap
    from ..rules.monarch_movement_replacements import (
        MONARCH_MOVEMENT_TEMPLATE, MONARCH_MOVEMENT_UPKEEP, monarch_movement_effect, monarch_movement_target_schema,
    )

    if (binding.event.value != "step.begin" or binding.active_zone != "battlefield" or body != binding.body
            or FrozenMap(binding.event_condition or {}) != MONARCH_MOVEMENT_UPKEEP):
        return None
    material = " ".join(body.split()).casefold().replace("’", "'")
    variants = {
        ("you may put a land card from your hand onto the battlefield. if you're the monarch, "
         "instead you may put a creature or land card from your hand onto the battlefield."): "hand_entry",
        ("return target permanent card with mana value 3 or less from your graveyard to your hand. "
         "if you're the monarch, return that permanent card to the battlefield instead."): "graveyard_destination",
    }
    mode = variants.get(material)
    if mode is None:
        return None
    effect = monarch_movement_effect(mode)
    mechanics = ("fixed-resolution-public-condition",
                 *effect["mechanic_ids"], *effect["otherwise_mechanic_ids"])
    return MONARCH_MOVEMENT_TEMPLATE, (effect,), monarch_movement_target_schema(mode), tuple(dict.fromkeys(mechanics))


def _entrant_power_branch_result(binding, body):
    from ..replacement.immutable import FrozenMap
    from ..rules.entrant_power_branch import CONTROLLED_CREATURE_ENTRY, entrant_power_branch_effect

    if (binding.active_zone != "battlefield" or binding.event.value != "creature.enter" or body != binding.body
            or FrozenMap(binding.event_condition or {}) != FrozenMap(CONTROLLED_CREATURE_ENTRY)):
        return None
    pattern = (
        r"Draw a card if its power is (?P<minimum>[1-9]) or greater\. "
        r"Otherwise, put (?P<counters>one \+1/\+1 counter|two \+1/\+1 counters|three \+1/\+1 counters) on it\."
    )
    match = re.fullmatch(pattern, " ".join(body.split()), re.I)
    if not match:
        return None
    count = {"one": 1, "two": 2, "three": 3}[match["counters"].split()[0].casefold()]
    effect = entrant_power_branch_effect(int(match["minimum"]), count)
    return ("entrant-power-draw-or-counters-v1", (effect,), None, ("fixed-resolution-public-condition", "cr-122-counters"))


def opponent_source_power_mana_result(binding, body, *, card_name):
    from ..replacement.immutable import FrozenMap
    from ..rules.opponent_event_mana_payment import (
        opponent_source_power_mana_effect, opponent_source_power_payment_condition,
    )
    from ..rules.source_references import SourceReferenceSpec
    from .draw_templates import fixed_draw_effect_template
    from .optional_payment_templates import FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC
    from .oracle_source_text import without_parenthetical_reminder

    if (binding.active_zone != "battlefield" or binding.event.value != "spell.cast"
            or body != binding.body
            or FrozenMap(binding.event_condition) != FrozenMap(opponent_source_power_payment_condition())):
        return None
    named = "|" + SourceReferenceSpec(card_name).regex_pattern if card_name else ""
    pattern = (
        r"Draw a card unless that player pays \{X\}, where X is "
        rf"(?:this creature{named})['’]s power\."
    )
    if re.fullmatch(pattern, " ".join(without_parenthetical_reminder(body).split()), re.I) is None:
        return None
    _, _, _, draw_mechanics = fixed_draw_effect_template("Draw a card.")
    return (
        "opponent-source-power-mana-payment-v1", (opponent_source_power_mana_effect(),), None,
        (FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC, *draw_mechanics),
    )


def opponent_event_mana_result(binding, body):
    from ..rules.opponent_event_mana_payment import (
        opponent_event_mana_payment_effect, opponent_event_payment_conditions_match,
    )
    from .draw_templates import fixed_draw_effect_template
    from .optional_payment_templates import FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC
    from .oracle_source_text import without_parenthetical_reminder
    from .token_templates import fixed_token_creation_effect_template

    if binding.active_zone != "battlefield" or body != binding.body:
        return None
    material = " ".join(without_parenthetical_reminder(body).split())
    if binding.event.value == "spell.cast":
        match = re.fullmatch(r"You may draw a card unless that player pays \{(?P<price>[1-9]|10)\}\.", material, re.I)
        if match is None:
            return None
        result = fixed_draw_effect_template("You may draw a card.")
    elif binding.event.value == "card.drawn":
        match = re.fullmatch(
            r"That player may pay \{(?P<price>[1-9]|10)\}\. If the player doesn['’]t, you create a Treasure token\.",
            material, re.I,
        )
        if match is None:
            return None
        result = fixed_token_creation_effect_template("Create a Treasure token.").compiled()
    else:
        return None
    _, (child,), _, mechanics = result
    if not opponent_event_payment_conditions_match(binding.event.value, binding.event_condition, child):
        return None
    effect = opponent_event_mana_payment_effect(int(match["price"]), child)
    return "fixed-opponent-event-mana-payment-v1", (effect,), None, (FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC, *mechanics)


def _bound_source_specific_result(binding, body, *, card_name, effect_template):
    from .counter_movement_templates import fixed_counter_movement_trigger_result
    from .opponent_top_exile_templates import opponent_top_exile_trigger_result
    from .fixed_control_templates import attached_temporary_control_trigger_result
    from .resolution_condition_templates import first_ability_counter_trigger_result, second_ability_reveal_trigger_result
    from .opponent_library_exile_templates import (
        controlled_opponent_library_exile_play_result,
        each_opponent_library_top_exile_result,
    )
    from .opposing_owner_entry_life import opposing_owner_entry_result

    from .temporary_player_rule_templates import turn_creature_target_prohibition_trigger_template
    if prohibition := turn_creature_target_prohibition_trigger_template(binding, body):
        return prohibition, False

    from .land_entry_library_exile import land_entry_top_exile_result
    if land_top := land_entry_top_exile_result(binding, body):
        return land_top, False
    from .fixed_resolution_mana import source_attack_turn_retained_mana_result

    if attack_mana := source_attack_turn_retained_mana_result(binding, body):
        return attack_mana, False
    if defending_top := defending_player_top_exile_result(binding, body):
        return defending_top, True
    if each_opponent_exile := each_opponent_library_top_exile_result(binding, body):
        return each_opponent_exile, True
    if opponent_library_play := controlled_opponent_library_exile_play_result(binding, body):
        return opponent_library_play, True
    if opponent_top := opponent_top_exile_trigger_result(binding, body):
        return opponent_top, True
    if owned_entry := opposing_owner_entry_result(binding, body):
        return owned_entry, True
    if movement := fixed_counter_movement_trigger_result(binding, body):
        return movement, True
    if counter := first_ability_counter_trigger_result(
        binding, body, compile_component=lambda text: effect_template(text, card_name=card_name),
    ):
        return counter, False
    if reveal := second_ability_reveal_trigger_result(binding, body):
        return reveal, False
    if search := _upkeep_opponent_threshold_search_result(binding, body):
        return search, False
    if monarch_distribution := monarch_counter_distribution_result(binding, body):
        return monarch_distribution, True
    if monarch_knight := monarch_knight_entry_damage_result(binding, body, card_name=card_name):
        return monarch_knight, True
    if monarch_mill := monarch_selected_player_mill_result(binding, body):
        return monarch_mill, True
    if movement := monarch_movement_replacement_result(binding, body):
        return movement, True
    if power_branch := _entrant_power_branch_result(binding, body):
        return power_branch, False
    if payment := (opponent_source_power_mana_result(binding, body, card_name=card_name)
                   or opponent_event_mana_result(binding, body)):
        return payment, False
    if attached_control := attached_temporary_control_trigger_result(binding, body):
        return attached_control
    if colored_counter := _equipped_color_upkeep_counter(
        binding, body, compile_effect=lambda text: effect_template(text, card_name=card_name),
    ):
        return colored_counter
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
    from .attack_event_results import attached_player_attack_token_result
    if attack_tokens := attached_player_attack_token_result(binding, body):
        return attack_tokens, False
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
    from .private_opponent_library_cast_templates import private_cast_or_scheduled_result
    scheduled=private_cast_or_scheduled_result(binding,body,effect_template=effect_template,card_name=card_name)
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
