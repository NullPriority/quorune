from __future__ import annotations

"""Bind source-bound typed instructions before generic stack resolution."""

from .. import regeneration
from .context import SemanticNodeError


def _bind_opponent_draw_result(host, item, raw_effect):
    from ..drawing.transaction import begin_opponent_draw, opponent_draw_reward
    from ..rules.opponent_draw_result import is_closed_opponent_draw_program, opponent_draw_effects

    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    opening, closing = opponent_draw_effects()
    if (
        program is None or program.trust_level != "trusted" or not is_closed_opponent_draw_program(program)
        or item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True
        or item.targets or type(raw_effect.get("schema_version")) is not int or raw_effect not in (opening, closing)
    ):
        raise ValueError("The distinct opponent draw result lost its original resolving spell")
    return begin_opponent_draw(item) if raw_effect == opening else opponent_draw_reward(item)


def prepare_source_bound_resolution(host, item, raw_effect, instruction_pointer):
    """Resolve typed source-bound instructions before generic resolution binding."""
    from ..rules.die_damage_table import (
        SPARED_OUTCOME_FIELD, TABLE_FIELD, bind_die_damage_spared_outcome, bind_die_damage_table,
    )

    try:
        if TABLE_FIELD in raw_effect:
            return True, bind_die_damage_table(host, item, raw_effect, instruction_pointer=instruction_pointer)
        if SPARED_OUTCOME_FIELD in raw_effect:
            return True, bind_die_damage_spared_outcome(host, item, raw_effect)
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    from ..rules.attached_copy_payment import uses_attached_copy_payment

    if uses_attached_copy_payment(raw_effect):
        from ..token_copy_references import bind_attached_copy_payment

        try:
            return True, bind_attached_copy_payment(host, item, raw_effect)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.upkeep_power_counter_balance import bind_upkeep_power_counter_balance, has_upkeep_power_counter_effect

    if has_upkeep_power_counter_effect(raw_effect):
        try:
            return True, bind_upkeep_power_counter_balance(host, item, raw_effect)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rad_counters import bind_rad_instruction, is_rad_instruction
    from ..rules.controlled_creature_counter_tokens import COUNT_FIELD, bind_controlled_creature_counter_tokens

    if COUNT_FIELD in raw_effect:
        try:
            return True, bind_controlled_creature_counter_tokens(host, item, raw_effect)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc

    if is_rad_instruction(raw_effect):
        return True, bind_rad_instruction(host, item, raw_effect)
    from ..rules.opponent_draw_result import OPPONENT_DRAW_RESULT_FIELD

    if OPPONENT_DRAW_RESULT_FIELD in raw_effect:
        try:
            return True, _bind_opponent_draw_result(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.selected_discard_draw import SELECTED_DISCARD_DRAW_FIELD, bind_selected_discard_draw

    if raw_effect.get("op") == "draw" and SELECTED_DISCARD_DRAW_FIELD in raw_effect:
        try:
            return True, bind_selected_discard_draw(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    from ..rules.watched_combat_draw import WATCHED_COMBAT_DRAW_FIELD, bind_watched_combat_draw

    if WATCHED_COMBAT_DRAW_FIELD in raw_effect:
        try:
            return True, bind_watched_combat_draw(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    from ..rules.controlled_combat_tokens import COMBAT_TOKEN_FIELD, bind_controlled_combat_token

    if COMBAT_TOKEN_FIELD in raw_effect:
        try:
            return True, bind_controlled_combat_token(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    from ..rules.opponent_draw_followup import DISTINCT_DRAW_PHASE

    if DISTINCT_DRAW_PHASE in raw_effect:
        from ..drawing.transaction import prepare_distinct_draw_followup

        try:
            return True, prepare_distinct_draw_followup(host, item, raw_effect, instruction_pointer)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.faction_trigger_model import FACTION_RESULT_FIELD

    if FACTION_RESULT_FIELD in raw_effect:
        from ..rules.faction_trigger_results import prepare_faction_derived_effect

        try:
            return True, prepare_faction_derived_effect(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.equipped_attack_die import DRAW_FIELD, ROLL_FIELD as EQUIPPED_ROLL_FIELD, bind_equipped_attack_die

    if EQUIPPED_ROLL_FIELD in raw_effect or DRAW_FIELD in raw_effect:
        try:
            return True, bind_equipped_attack_die(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.die_roll_draw import ROLL_FIELD, bind_die_draw
    from ..rules.targeted_attack_die_copy import (
        ROLL_OPERATION, bind_targeted_attack_die_copy, is_repeat_offer, is_rolled_attacking_copy,
        validate_attacking_copy,
    )

    if raw_effect.get("op") == ROLL_OPERATION:
        try:
            return True, bind_targeted_attack_die_copy(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if is_rolled_attacking_copy(raw_effect):
        try:
            validate_attacking_copy(raw_effect, item.controller)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return True, dict(raw_effect)
    if is_repeat_offer(raw_effect):
        # The repeat already carries its bound, opaque copiable values.
        return True, dict(raw_effect)

    if ROLL_FIELD in raw_effect:
        try:
            return True, bind_die_draw(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.black_devotion_library_search import BOUND_FIELD, bind_black_devotion_search

    if BOUND_FIELD in raw_effect:
        try:
            return True, bind_black_devotion_search(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.ordered_source_owner_hand_entry import FIELD as OWNER_DRAW_LAND_FIELD, bind_original_owner_draw_land
    from ..rules.die_roll_library_depth import FIELD as DIE_DEPTH_FIELD, bind_die_library_depth

    try:
        if OWNER_DRAW_LAND_FIELD in raw_effect:
            return True, bind_original_owner_draw_land(host, item, raw_effect)
        if DIE_DEPTH_FIELD in raw_effect:
            return True, bind_die_library_depth(host, item, raw_effect, instruction_pointer=instruction_pointer)
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    from ..stack_target_change_model import POWER_ADJUSTED_RETARGET_ROLL

    if POWER_ADJUSTED_RETARGET_ROLL in raw_effect:
        from ..rules.stack_target_change_roll import bind_power_adjusted_retarget_roll

        try:
            return True, bind_power_adjusted_retarget_roll(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    if regeneration.is_controlled_creature_regeneration(raw_effect):
        return True, regeneration.bind_controlled_creature_regeneration(host, item, raw_effect)
    return False, None
