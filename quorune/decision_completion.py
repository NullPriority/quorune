from __future__ import annotations

"""Route the existing completed decisions to their authoritative owners."""

from .errors import GameRuleError
from .aura import complete_aura_entry_choice
from .replacement_decisions import complete_replacement_order_choice
from .trigger_processing import complete_trigger_order_decision


def dispatch_completed_decision(host,decision):
    kind=decision.kind
    if kind=='mulligan.declare':host._complete_mulligan_declaration(decision)
    elif kind=='mulligan.bottom':host._complete_mulligan_bottom(decision)
    elif kind=='opening.hand':
        from .opening_hand import complete_opening_hand_action
        complete_opening_hand_action(host,decision)
    elif kind=='priority':host._complete_priority(decision)
    elif kind=='combat.attackers':host._complete_attackers(decision)
    elif kind=='untap.optional_source':
        from .optional_untap import complete_optional_untap_choice
        complete_optional_untap_choice(host,decision)
    elif kind=='combat.blockers':host._complete_blockers(decision)
    elif kind=='combat.damage':host._complete_combat_damage(decision)
    elif kind=='cleanup.discard':host._complete_cleanup_discard(decision)
    elif kind=='state.legend':host._complete_legend_choice(decision)
    elif kind=='state.commander_zone':host._complete_commander_zone_choice(decision)
    elif kind=='state.battle_protector':host._complete_battle_protector_choice(decision)
    elif kind=='battle.enter_protector':host._complete_battle_entry_protector_choice(decision)
    elif kind=='battle.siege_defeated':host._complete_siege_defeated_choice(decision)
    elif kind=='selection.exile_cast':host._complete_one_shot_exile_cast_choice(decision)
    elif kind=='choice.apnap':host._complete_apnap_choice(decision)
    elif kind=='replacement.order':complete_replacement_order_choice(host,decision,error_type=GameRuleError)
    elif kind=='aura.entry':complete_aura_entry_choice(host,decision,error_type=GameRuleError)
    elif kind=='trigger.order':complete_trigger_order_decision(host,decision)
    elif kind=='permanent.timestamp_order':
        from .transform_timestamp_order import complete_transform_timestamp_order
        complete_transform_timestamp_order(host,decision)
    elif kind=='arbiter.resolve':host._complete_arbiter_resolution(decision)
    elif kind=='search.fetch':host._complete_fetch_choice(decision)
    elif kind=='semantic.target':host._complete_semantic_target(decision)
    elif kind=='semantic.choice':host._complete_semantic_choice(decision)
    elif kind=='semantic.search':host._complete_semantic_search(decision)
    elif kind=='semantic.storm':host._complete_storm_choice(decision)
    elif kind in {'draw.replacement','draw.reveal'}:host._complete_draw_replacement(decision)
    else:raise GameRuleError(f'Unsupported completed decision {kind}')
