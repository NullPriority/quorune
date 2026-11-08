from __future__ import annotations
from .received_damage_bindings import RECEIVED_PERMANENT_VARIANTS
from .designation_event_bindings import DESIGNATION_EVENT_VARIANTS
from ..damage_recipient_model import DAMAGE_PERMANENT_VARIANT

"""Finite public event variants admitted by the shared result composer."""

PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS = frozenset(
    {
        'source_attacks_unblocked',
        "source_transforms_into",
        "controlled_permanent_transforms_into",
        DAMAGE_PERMANENT_VARIANT,
        'source_damage_death_history',
        'public_anywhere_graveyard_card',
        'public_land_play',
        'singular_qualified_attack',
        'controlled_token_enters',
        "another_controlled_colorless_creature_enters",
        "another_controlled_creature_power_at_most",
        "controlled_enchantment_graveyard",
        "controller_cycles_another",
        "enchanted_creature_damage_an_opponent",
        "enchanted_creature_dies",
        "enchanted_permanent_graveyard",
        "equipped_creature_attacks",
        "equipped_creature_combat_damage_a_player",
        *{
            f"{relation}_creature_{combat}damage{recipient}"
            for relation in ("enchanted", "equipped")
            for combat in ("", "combat_")
            for recipient in ("", "_a_player", "_an_opponent")
        },
        "equipped_creature_dies",
        "one_or_more_controller_creature_cards_leave_graveyard",
        "source_or_controlled_creature_or_artifact_dies",
        "this_creature_becomes_blocked_by_creature",
        "a_creature_you_control_becomes_blocked",
        "a_creature_blocks",
        "the end step",
        "each combat",
    }
) | RECEIVED_PERMANENT_VARIANTS | DESIGNATION_EVENT_VARIANTS
