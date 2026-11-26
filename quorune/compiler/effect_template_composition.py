from __future__ import annotations

"""Reviewed composition of closed, typed effect templates."""

from typing import Any, Callable, Mapping
from functools import partial
from . import regex_working_set as re

from ..next_turn_effect import NEXT_TURN_EFFECT_FIELD, supports_next_turn_effect
from .closed_effect_programs import closed_effect_program_template
from .extra_turn_templates import fixed_end_turn_effect_template
from .bound_effect_programs import bound_effect_program_template
from .fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
from .blight_templates import normalized_blight_sequence
from .public_query_effect_amounts import public_query_effect_amount_template
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template
from .source_tap_characteristics import tapped_source_characteristics_template
from .source_tap_duration import (
    controlled_source_untap_restraint_template, match_tapped_source_duration, tapped_source_untap_restraint_template,
)
from .fixed_resolution_characteristics import fixed_resolution_characteristics_effect_template
from .fixed_effect_payment_templates import fixed_effect_payment_template,fixed_effect_payment_with_mandatory_prefix
from .reflexive_payment_templates import reflexive_payment_template
from .optional_payment_templates import fixed_optional_mana_payment_template
from .counter_templates import targeted_controller_payment_followup_template, targeted_controller_payment_template
from .whole_hand_counter_payment import targeted_whole_hand_counter_payment_template
from .token_copy_templates import token_copy_recipe_template
from .resolution_condition_templates import resolution_condition_template
from .query_counter_sequences import ordered_quantity_sequence_template
from .oracle_source_text import without_parenthetical_reminder


CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[[str], CompiledEffectTemplate]


_NEXT_TURN_DURATION = re.compile(r"\buntil your next turn\b", re.IGNORECASE)


def _next_turn_effect_template(
    text: str, compile_atomic: EffectCompiler, *, card_name: str, allow_source: bool,
) -> CompiledEffectTemplate | None:
    """Match the duration wording before delegating the full result grammar."""
    if "until your next turn" not in text.casefold():
        return None
    body = without_parenthetical_reminder(text)
    folded = body.casefold()
    if (not (folded.startswith("until your next turn, ") or folded.rstrip(".").endswith(" until your next turn"))
            or "until end of turn" in folded or '"' in body):
        return None
    equivalent, count = _NEXT_TURN_DURATION.subn("until end of turn", body)
    if count != 1:
        return None
    from .target_characteristic_sets import target_characteristic_set_template

    template_id, effects, schema, mechanics = (
        target_characteristic_set_template(equivalent, include_single=True) or compile_atomic(equivalent)
    )
    if template_id is None:
        from .temporary_declaration_templates import temporary_declaration_restriction_effect_template

        declaration_body = (body[len("Until your next turn, "):].rstrip(".") + " this turn."
                            if folded.startswith("until your next turn, ")
                            else _NEXT_TURN_DURATION.sub("this turn", body))
        declaration = temporary_declaration_restriction_effect_template(
            declaration_body, card_name=card_name, allow_source=allow_source,
        )
        if declaration is not None:
            template_id, effects, schema, mechanics = declaration.compiled()
    if template_id is None or len(effects) != 1 or not supports_next_turn_effect(effects[0]):
        return None
    return f"{template_id}-next-turn", ({**effects[0], NEXT_TURN_EFFECT_FIELD: True},), schema, mechanics


def _damage_life_result(body, card_name, source_context, compile_fixed):
    from .damage_templates import fixed_damage_dealt_life_template, targeted_dealt_life_gain_template

    permanent = source_context.get("source_is_permanent") is True
    fixed = fixed_damage_dealt_life_template(body, source_name=card_name, source_is_permanent=permanent)
    if fixed is not None or permanent:
        return fixed
    query_damage = partial(
        public_query_effect_amount_template, source_name=card_name,
        compile_fixed=partial(compile_fixed, card_name=card_name, **source_context),
    )
    return targeted_dealt_life_gain_template(body, compile_damage=query_damage)


def _spell_only_result(text: str, compile_atomic: EffectCompiler, compile_payment: EffectCompiler):
    from .exile_until_same_name_templates import exile_until_same_name_template
    from .owned_commander_amount import owned_commander_amount_template
    from .resolution_condition_templates import other_spell_draw_template
    from .targeted_discard_spell_copy import targeted_discard_spell_copy_template
    from .token_creation_reflexive_templates import token_creation_reflexive_template
    from .targeted_turn_target_prohibition import targeted_turn_prohibition_template

    return (
        exile_until_same_name_template(text)
        or targeted_turn_prohibition_template(text)
        or other_spell_draw_template(text, compile_component=compile_atomic)
        or owned_commander_amount_template(text)
        or token_creation_reflexive_template(text, compile_effect=compile_payment)
        or targeted_discard_spell_copy_template(text)
    )


def _whole_contextual_effect_template(text):
    from ..temporary_graveyard_escape import targeted_escape_template

    return fixed_end_turn_effect_template(text) or targeted_escape_template(text)


def reviewed_contextual_effect_template(
    text: str, *, card_name: str,
    compile_atomic: Callable[..., CompiledEffectTemplate],
    compile_fixed: Callable[..., CompiledEffectTemplate],
    cast_x_available: bool = False, forbid_public_x: bool = False,
    kicked_self_replacement_available: bool = False,
    bargain_available: bool = False,
    **source_context: Any,
) -> CompiledEffectTemplate:
    """Bind source context before composing the same closed leaf owners."""
    # These instructions own the complete resolving operation.
    whole = _whole_contextual_effect_template(text)
    if whole is not None:
        return whole

    def atomic_with_characteristics(body: str) -> CompiledEffectTemplate:
        if dealt_life := _damage_life_result(body, card_name, source_context, compile_fixed):
            return dealt_life
        from .mana_value_destruction import mana_value_destruction_template
        mana_value=mana_value_destruction_template(body,source_name=card_name,cast_x_available=cast_x_available)
        if mana_value is not None:return mana_value
        if controlled_restraint := controlled_source_untap_restraint_template(body, source_context=source_context, card_name=card_name):
            return controlled_restraint
        duration = match_tapped_source_duration(body, card_name=card_name, source_context=source_context)
        if duration is not None:
            tapped = tapped_source_untap_restraint_template(duration) or tapped_source_characteristics_template(
                duration, compile_atomic=atomic_with_characteristics,
            )
            if tapped is not None:
                return tapped
        timed = _next_turn_effect_template(
            body, atomic_with_characteristics, card_name=card_name,
            allow_source=source_context.get("source_is_permanent") is True,
        )
        if timed is not None:
            return timed
        from .reveal_until_library_selection_templates import reveal_until_library_selection_effect_template
        reveal_until=reveal_until_library_selection_effect_template(body,
            allow_reflexive=source_context.get('source_is_permanent') is False)
        if reveal_until is not None:return reveal_until
        from .discover_templates import discover_effect_template
        discover=discover_effect_template(body,source_name=card_name,cast_x_available=cast_x_available,
            forbid_public_x=forbid_public_x,**source_context)
        if discover is not None:return discover
        from .library_battlefield_templates import library_battlefield_selection_effect_template
        battlefield=library_battlefield_selection_effect_template(body)
        if battlefield is not None:return battlefield
        from .variable_library_selection_templates import variable_library_selection_effect_template
        inspection=variable_library_selection_effect_template(body,source_name=card_name,
            cast_x_available=cast_x_available,forbid_public_x=forbid_public_x,**source_context)
        if inspection is not None:return inspection
        from .incubate_templates import incubate_effect_template
        incubate=incubate_effect_template(body,source_name=card_name,cast_x_available=cast_x_available,
            forbid_public_x=forbid_public_x,**source_context)
        if incubate is not None:return incubate
        from .created_token_counters import created_token_counter_effect_template
        created=created_token_counter_effect_template(body,source_name=card_name,cast_x_available=cast_x_available,
            forbid_public_x=forbid_public_x,**source_context)
        if created is not None:return created
        from .temporary_keyword_removal_templates import temporary_keyword_removal_template
        removal=temporary_keyword_removal_template(body,source_name=card_name,
            source_is_permanent=source_context.get('source_is_permanent'),source_card_types=tuple(source_context.get('source_card_types',())))
        if removal is not None:return removal
        controller_payment = targeted_controller_payment_template(
            body, cast_x_available=cast_x_available, source_name=card_name,
        )
        if controller_payment is not None:
            return controller_payment.compiled()
        followup = targeted_controller_payment_followup_template(
            body, source_name=card_name, cast_x_available=cast_x_available,
            compile_component=lambda clause: compile_fixed(clause, card_name=card_name, **source_context),
        )
        if followup is not None:
            return followup
        from .stack_counter_followup import stack_counter_followup_template
        counter_followup = stack_counter_followup_template(
            body, compile_component=lambda clause: reviewed_effect_template_composition(
                clause, source_name=card_name,
                compile_atomic=lambda value: compile_atomic(value, card_name=card_name, **source_context),
                compile_fixed=lambda value: compile_fixed(value, card_name=card_name, **source_context),
            ),
        )
        if counter_followup is not None:
            return counter_followup
        current = compile_atomic(body, card_name=card_name, **source_context)
        if current[0] is not None:
            return current
        copy_recipe = token_copy_recipe_template(body, source_name=card_name,
            source_is_permanent=source_context.get("source_is_permanent"),
            source_attachment_relation=source_context.get("source_attachment_relation"))
        if copy_recipe is not None:
            return copy_recipe.compiled()
        return fixed_resolution_characteristics_effect_template(
            body,
            source_name=card_name,
            source_is_permanent=source_context.get("source_is_permanent"),
            source_card_types=tuple(source_context.get("source_card_types", ())),
            source_attachment_relation=source_context.get("source_attachment_relation"),
            cast_x_available=cast_x_available,
        ) or current

    if bargain_available is True:
        from .bargain_conditions import bargain_same_target_template
        declared = bargain_same_target_template(text, source_name=card_name, compile_component=atomic_with_characteristics)
        if declared is not None:return declared
    if kicked_self_replacement_available is True:
        from .kicked_self_replacement_templates import kicked_self_replacement_template
        replacement=kicked_self_replacement_template(text,source_name=card_name,compile_component=atomic_with_characteristics)
        if replacement is not None:
            return replacement

    # Preserve the certified v1 one-mana/one-leaf payload before using v2.
    from .source_animation_prevention import source_animation_prevention_template
    animation = source_animation_prevention_template(text, card_name=card_name,
        source_context=source_context, compile_clause=atomic_with_characteristics)
    if animation is not None:return animation
    legacy_payment=fixed_optional_mana_payment_template(text,compile_effect=atomic_with_characteristics)
    if legacy_payment is not None:
        from ..rules.fixed_effect_clause_shapes import fixed_optional_mana_payment_node_capabilities
        if fixed_optional_mana_payment_node_capabilities(effects=legacy_payment.effects,target_schema=legacy_payment.target_schema,mechanic_ids=legacy_payment.mechanics):
            return legacy_payment.compiled()
    def payment_body(body: str) -> CompiledEffectTemplate:
        return reviewed_effect_template_composition(body,source_name=card_name,
            compile_atomic=atomic_with_characteristics,
            compile_fixed=partial(compile_fixed,card_name=card_name,**source_context))
    reflexive = reflexive_payment_template(text, compile_effect=payment_body)
    if reflexive is not None:
        return reflexive
    payment_context = {"compile_effect": payment_body, "source_is_permanent": source_context.get("source_is_permanent")}
    from .targeted_life_decline import targeted_life_decline_template
    fixed_payment = (
        targeted_life_decline_template(text)
        or fixed_effect_payment_template(text, **payment_context)
        or fixed_effect_payment_with_mandatory_prefix(text, **payment_context)
    )
    if fixed_payment is not None:
        return fixed_payment
    def compose(body: str, *, card_name: str) -> CompiledEffectTemplate:
        return reviewed_effect_template_composition(
            body, source_name=card_name,
            cast_x_available=cast_x_available, forbid_public_x=forbid_public_x,
            compile_atomic=atomic_with_characteristics,
            compile_fixed=partial(compile_fixed, card_name=card_name, **source_context),
        )

    if source_context.get("source_is_permanent") is False:
        spell_only = targeted_whole_hand_counter_payment_template(text) or _spell_only_result(text, atomic_with_characteristics, payment_body)
        if spell_only is not None:
            return spell_only
    compiled = compose(text, card_name=card_name)
    if compiled[0] is None and source_context.get("source_is_permanent") is True:
        from .source_self_effect_templates import source_declaration_sequence
        return source_declaration_sequence(text, card_name=card_name, compile_effect=compose) or compiled
    return compiled


def reviewed_effect_template_composition(
    text: str,
    *,
    source_name: str,
    compile_atomic: EffectCompiler,
    compile_fixed: EffectCompiler,
    cast_x_available: bool = False,
    forbid_public_x: bool = False,
    allow_declarations: bool = True,
    allow_conditions: bool = True,
) -> CompiledEffectTemplate:
    """Preserve owned linked instructions before generic clause composition."""
    from .permanent_resolution_choices import permanent_resolution_choice_template
    from .mill_templates import committed_mill_card_template

    if chosen_permanent := permanent_resolution_choice_template(text):
        return chosen_permanent
    if committed_mill := committed_mill_card_template(text):
        return committed_mill
    if cast_x_available and text.casefold().startswith("each player mills x cards."):
        from .mill_templates import committed_milled_creature_token_template
        if milled := committed_milled_creature_token_template(text):
            return milled
    from .graveyard_exile_branch_templates import graveyard_exile_branch_template

    from .graveyard_exile_token_templates import graveyard_exile_token_template
    from .graveyard_exile_type_outcome_templates import graveyard_exile_type_outcome_template

    owned_exile = (graveyard_exile_branch_template(text, source_name=source_name) or graveyard_exile_token_template(text)
        or graveyard_exile_type_outcome_template(text))
    if owned_exile is not None:
        return owned_exile
    from .current_life_effect_amounts import current_life_effect_amount_template
    current_life=current_life_effect_amount_template(text,source_name=source_name,compile_fixed=compile_atomic)
    if current_life is not None:return current_life
    from .turn_life_loss_recovery import turn_life_loss_recovery_template
    recovery=turn_life_loss_recovery_template(text,compile_fixed=compile_atomic)
    if recovery is not None:return recovery
    from .per_player_query_life import per_player_query_life_template
    per_player_loss=per_player_query_life_template(text,compile_fixed=compile_atomic,source_name=source_name)
    if per_player_loss is not None:return per_player_loss
    from .per_player_query_damage import per_player_query_damage_template
    per_player_damage=per_player_query_damage_template(text,compile_fixed=compile_atomic,source_name=source_name)
    if per_player_damage is not None:return per_player_damage
    from .per_player_life_damage import per_player_life_damage_template
    life_damage=per_player_life_damage_template(text,compile_fixed=compile_atomic,source_name=source_name)
    if life_damage is not None:return life_damage
    from .target_player_query_amounts import target_player_query_amount_template
    target_query=target_player_query_amount_template(text,compile_fixed=compile_atomic,source_name=source_name)
    if target_query is not None:return target_query
    from .paid_alternative_cast_results import paid_alternative_spell_result_template
    paid_alternative=paid_alternative_spell_result_template(text,compile_component=compile_fixed)
    if paid_alternative is not None:return paid_alternative
    from .colored_hand_reveal_templates import colored_hand_reveal_template
    colored_reveal=colored_hand_reveal_template(text,compile_fixed=compile_atomic)
    if colored_reveal is not None:return colored_reveal
    from .named_search_templates import named_zone_search_sequence_template
    context = dict(source_name=source_name, compile_atomic=compile_atomic, compile_fixed=compile_fixed,
        cast_x_available=cast_x_available, forbid_public_x=forbid_public_x,
        allow_declarations=allow_declarations, allow_conditions=allow_conditions)
    from .optional_target_result_templates import optional_target_result_template
    optional_target = optional_target_result_template(text,
        compile_mandatory=partial(reviewed_effect_template_composition, **context))
    if optional_target is not None:return optional_target.compiled()
    named = named_zone_search_sequence_template(text,
        compile_prefix=partial(reviewed_effect_template_composition, **context))
    return named if named is not None else _reviewed_effect_template_composition(text, **context)


def _has_committed_followup(text):
    return (
        "destroyed this way" in text or "died this way" in text or "dies this way" in text or "exiled this way" in text
        or "permanent put into a graveyard this way, its controller creates" in text
        or "if that land was nonbasic, untap this creature" in text
        or text.startswith("exile all attacking creatures target player controls.")
    )


def _reviewed_effect_template_composition(
    text: str,
    *,
    source_name: str,
    compile_atomic: EffectCompiler,
    compile_fixed: EffectCompiler,
    cast_x_available: bool = False,
    forbid_public_x: bool = False,
    allow_declarations: bool = True,
    allow_conditions: bool = True,
) -> CompiledEffectTemplate:
    """Compile one reviewed effect or a closed composition of those effects."""

    from .face_up_effect_templates import targeted_face_up_template
    face_up=targeted_face_up_template(text)
    if face_up is not None:return face_up
    from .fixed_stack_spell_copy import fixed_stack_spell_copy_template
    spell_copy = fixed_stack_spell_copy_template(text)
    if spell_copy is not None:
        return spell_copy
    atomic = compile_atomic(text)
    if atomic[0] is not None:
        return atomic
    folded = text.casefold()
    if 'would die this turn, exile it instead.' in folded:
        from .damage_death_exile_templates import damage_death_exile_template

        death_exile = damage_death_exile_template(text, compile_body=partial(
            reviewed_effect_template_composition, source_name=source_name,
            compile_atomic=compile_atomic, compile_fixed=compile_fixed, cast_x_available=cast_x_available,
            forbid_public_x=forbid_public_x, allow_declarations=allow_declarations, allow_conditions=allow_conditions,
        ))
        if death_exile is not None:
            return death_exile
    if "the top card" in folded:
        from .revealed_library_top_templates import public_top_reveal_template

        if public_top_reveal := public_top_reveal_template(text, folded=folded, compile_literal=compile_atomic):
            return public_top_reveal
    if _has_committed_followup(folded):
        from .destruction_followup_templates import destruction_followup_template
        from .public_exile_followup_templates import public_exile_followup_template
        from .targeted_exile_token_templates import targeted_exile_token_template

        compile_literal = partial(
            reviewed_effect_template_composition, source_name=source_name,
            compile_atomic=compile_atomic, compile_fixed=compile_fixed, cast_x_available=cast_x_available,
            forbid_public_x=forbid_public_x, allow_declarations=allow_declarations, allow_conditions=allow_conditions,
        )
        committed_followup = (
            destruction_followup_template(text, compile_literal=compile_literal)
            or public_exile_followup_template(text, compile_literal=compile_literal)
            or targeted_exile_token_template(text, compile_literal=compile_literal, cast_x_available=cast_x_available)
        )
        if committed_followup is not None:
            return committed_followup
    from .source_counter_quantity_sequences import source_counter_quantity_sequence
    source_quantity=source_counter_quantity_sequence(text,card_name=source_name,compile_fixed=compile_fixed)
    if source_quantity is not None:return source_quantity
    from .target_player_sequences import fixed_target_player_sequence_template
    player_sequence=fixed_target_player_sequence_template(text)
    if player_sequence is not None:return player_sequence
    from .target_set_result_sequences import target_set_result_sequence_template
    shared_set=target_set_result_sequence_template(text,compile_independent=compile_atomic)
    if shared_set is not None:return shared_set
    from .prepared_creature_power_damage import prepared_creature_power_damage_template
    prepared_damage = prepared_creature_power_damage_template(
        text, source_name=source_name, compile_atomic=compile_atomic,
    )
    if prepared_damage is not None:
        return prepared_damage
    from .quantified_counter_targets import quantified_counter_target_template
    counters=quantified_counter_target_template(text)
    if counters is not None:return counters
    from .target_characteristic_sets import target_characteristic_set_template
    targeted_set=target_characteristic_set_template(text)
    if targeted_set is not None:return targeted_set
    from .any_number_target_sets import any_number_player_result_template, any_number_target_set_template
    any_number = any_number_player_result_template(text, compile_independent=compile_atomic) or any_number_target_set_template(text)
    if any_number is not None:return any_number
    if allow_conditions:
        from .paid_mana_resolution_templates import paid_mana_resolution_template
        paid=paid_mana_resolution_template(text,source_name=source_name,compile_component=lambda body:reviewed_effect_template_composition(
            body,source_name=source_name,compile_atomic=compile_atomic,compile_fixed=compile_fixed,
            cast_x_available=cast_x_available,forbid_public_x=forbid_public_x,allow_conditions=False))
        if paid is not None:return paid
        from .kicked_spell_conditions import kicked_spell_condition_template
        kicked = kicked_spell_condition_template(text,source_name=source_name, compile_component=lambda body: reviewed_effect_template_composition(
            body, source_name=source_name, compile_atomic=compile_atomic, compile_fixed=compile_fixed,
            cast_x_available=cast_x_available, forbid_public_x=forbid_public_x, allow_conditions=False,
        ))
        if kicked is not None:
            return kicked
        conditional = resolution_condition_template(
            text, source_name=source_name,
            compile_component=lambda body: reviewed_effect_template_composition(
                body, source_name=source_name, compile_atomic=compile_atomic,
                compile_fixed=compile_fixed, cast_x_available=cast_x_available,
                forbid_public_x=forbid_public_x, allow_conditions=False,
            ),
        )
        if conditional is not None:
            return conditional
        from .sequential_public_conditions import sequential_public_condition_template
        sequential=sequential_public_condition_template(text,source_name=source_name,compile_component=lambda body:
            reviewed_effect_template_composition(body,source_name=source_name,compile_atomic=compile_atomic,
                compile_fixed=compile_fixed,cast_x_available=cast_x_available,forbid_public_x=forbid_public_x,allow_conditions=False))
        if sequential is not None:return sequential
    query_amount = public_query_effect_amount_template(
        text, source_name=source_name, compile_fixed=compile_fixed,
    )
    if query_amount is not None:
        return query_amount
    def compile_fixed_amount(body):
        return reviewed_effect_template_composition(
            body, source_name=source_name, compile_atomic=compile_atomic,
            compile_fixed=compile_fixed, allow_declarations=False,
        )

    if allow_declarations:
        scalar = scalar_effect_amount_template(
            text, source_name=source_name, compile_fixed=compile_fixed_amount,
        )
        if scalar is not None:
            return scalar
        compile_amount = partial(
            declared_effect_amount_template, source_name=source_name,
            cast_x_available=cast_x_available, forbid_public_x=forbid_public_x,
            compile_fixed=compile_fixed_amount,
        )
        declared = compile_amount(text)
        if declared is not None:
            return declared
    query_counters = ordered_quantity_sequence_template(text, source_name=source_name, compile_atomic=compile_atomic, compile_fixed=compile_fixed)
    if query_counters is not None:return query_counters
    from .announced_target_sets import announced_target_clause_compiler
    sequence = fixed_effect_clause_sequence_template(
        normalized_blight_sequence(text),
        compile_clause=announced_target_clause_compiler(
            compile_atomic, compile_amount=compile_amount if allow_declarations else None,
        ) if cast_x_available else compile_atomic,
    )
    if sequence is not None:
        return sequence.compiled()
    bound = bound_effect_program_template(text, compile_component=compile_atomic, source_name=source_name)
    if bound is not None:
        return bound.compiled()
    program = closed_effect_program_template(
        text,
        compile_component=compile_atomic,
    )
    if program is not None:return program.compiled()
    from .multisentence_effect_components import multisentence_effect_program
    return multisentence_effect_program(text, compile_component=compile_atomic) or atomic
