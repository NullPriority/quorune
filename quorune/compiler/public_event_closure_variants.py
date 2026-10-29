from __future__ import annotations

"""Finite public event variants admitted by the shared result composer."""

PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS = frozenset(
    {
        'source_damage_death_history',
        'public_anywhere_graveyard_card',
        'singular_qualified_attack',
        "another_controlled_colorless_creature_enters",
        "another_controlled_creature_power_at_most",
        "controlled_enchantment_graveyard",
        "controller_cycles_another",
        "enchanted_creature_damage_an_opponent",
        "enchanted_creature_dies",
        "enchanted_permanent_graveyard",
        "equipped_creature_attacks",
        "equipped_creature_combat_damage_a_player",
        "equipped_creature_dies",
        "one_or_more_controller_creature_cards_leave_graveyard",
        "source_or_controlled_creature_or_artifact_dies",
        "this_creature_becomes_blocked_by_creature",
        "the end step",
        "each combat",
    }
)
