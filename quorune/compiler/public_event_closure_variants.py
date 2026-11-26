from __future__ import annotations
from .received_damage_bindings import RECEIVED_PERMANENT_VARIANTS
from .designation_event_bindings import DESIGNATION_EVENT_VARIANTS
from ..damage_recipient_model import DAMAGE_PERMANENT_VARIANT
from ..rules.enchanted_departure_events import ENCHANTED_DEPARTURE_VARIANT
from ..attack_batch_model import DIRECT_PLAYER_ATTACK_VARIANTS
from ..dungeon_progress import DUNGEON_COMPLETION_TRIGGER_VARIANT
from ..token_copy_recipient_model import OTHER_NONTOKEN_CREATURE_ENTRY
from ..rules.event_subscriptions import COMBAT_PARTICIPANT_DAMAGE_VARIANT, GRAVEYARD_CREATURE_TRANSITIONS_VARIANT
from ..rules.source_power_opponent_damage import SOURCE_POWER_OPPONENT_DAMAGE_VARIANT

"""Finite public event variants admitted by the shared result composer."""

ATTACHED_CREATURE_ATTACK_VARIANTS = frozenset({"enchanted_creature_attacks", "equipped_creature_attacks"})

PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS = frozenset(
    {
        'source_attacks_unblocked',
        "source_transforms_into",
        "controlled_permanent_transforms_into",
        DAMAGE_PERMANENT_VARIANT,
        *DIRECT_PLAYER_ATTACK_VARIANTS,
        "controlled_face_down_permanent_enters",
        "controller_turns_up_permanent",
        "controlled_permanent_turned_face_up",
        "permanent_turned_face_up",
        DUNGEON_COMPLETION_TRIGGER_VARIANT,
        'source_damage_death_history',
        'public_anywhere_graveyard_card',
        'creature_graveyard_owner',
        'public_land_play',
        'other_land_play_source_sacrifice',
        "controller_life_loss_draw",
        "opponent_mana_tapped_land_next_untap",
        "opponent_second_draw",
        "any_player_second_draw",
        "any_player_card_draw_damage",
        'singular_qualified_attack',
        'controlled_token_enters',
        "another_controlled_colorless_creature_enters",
        OTHER_NONTOKEN_CREATURE_ENTRY,
        "another_controlled_creature_power_at_most",
        "source_or_controlled_toughness_greater_than_power_entry",
        "controlled_enchantment_graveyard",
        "opponent_controlled_artifact_graveyard",
        "controller_cycles_another",
        "enchanted_creature_damage_an_opponent",
        "enchanted_creature_dies",
        "enchanted_permanent_graveyard",
        ENCHANTED_DEPARTURE_VARIANT,
        *ATTACHED_CREATURE_ATTACK_VARIANTS,
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

MULTI_EVENT_BINDING_CLOSURE_VARIANTS = frozenset(
    {
        'source_block_pair_counterpart',
        "source_room_door_unlock",
        "controlled_enchantment_entry_or_room_unlock",
        "enchanted_creature_tap_or_received_damage",
        "independent_dual_event",
        "qualified_zone_event_pair",
        GRAVEYARD_CREATURE_TRANSITIONS_VARIANT,
        "source_enters_or_attacks",
        "source_enters_or_dies",
        "source_attacks_or_blocks",
        "source_blocks_or_becomes_blocked",
        "source_enters_or_leaves_the_battlefield",
        "source_enters_or_is_put_into_a_graveyard_from_the_battlefield",
        "source_enters_or_is_turned_face_up",
        "source_enters_or_deals_combat_damage_to_a_player",
        "source_enters_or_becomes_monstrous",
        "source_transform_pair",
        "source_artifact_enters_and_is_sacrificed",
        "enchanted_creature_attacks_or_blocks",
        "enchanted_creature_blocks_or_becomes_blocked",
        COMBAT_PARTICIPANT_DAMAGE_VARIANT,
        SOURCE_POWER_OPPONENT_DAMAGE_VARIANT,
        "source_attacks_or_blocks_with_controlled_dinosaur",
    }
)
