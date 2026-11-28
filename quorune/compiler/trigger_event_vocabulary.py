"""Closed normalized-event vocabulary shared by trigger binding consumers."""
from enum import Enum

from ..attack_batch_model import DIRECT_PLAYER_ATTACK_EVENT
from ..rules.attack_declaration_mana import EVENT as PLAYER_ATTACK_DECLARATION
from ..rules.nonmana_activation_copy_model import ABILITY_ACTIVATION_COPY_EVENT
from ..day_night_model import DAY_TOGGLE_EVENT
from ..discard_effect_origin import DISCARD_BATCH_EVENT
from ..rules.attacking_opponent_library_exile import ATTACKED_PLAYER_OR_WALKER_EVENT
from ..dungeon_progress import DUNGEON_COMPLETED_EVENT
from ..rules.event_subscriptions import GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, LAND_PLAY_EVENT, OWNED_ZONE_EXILE_EVENT
from ..rules.monarch_transition import MONARCH_BECAME_EVENT
from ..rules.committed_nonland_mill import MILL_EVENT
from ..life_change import FIRST_LIFE_GAIN_EVENT, LIFE_LOSS_EVENT
from ..rules.first_own_turn_life_loss import EVENT as FIRST_OWN_TURN_LIFE_LOSS_EVENT
from ..shared_type_doors import DOOR_UNLOCKED_EVENT
from ..source_sacrifice_model import UNATTACHMENT_EVENT
from ..saga_lifecycle import FINAL_SAGA_CHAPTER_RESOLVED_EVENT
from ..triggered_mana_model import LAND_MANA_TAP_EVENT, SOURCE_MANA_TAP_EVENT
from ..opponent_library_exile_model import OPPONENT_LIBRARY_EXILE_EVENT
from ..rules.phase_occurrence_model import PHASE_IN_EVENT


from ..rules.creature_ability_activation import CREATURE_ABILITY_ACTIVATED
from ..rules.gift_occurrence import GIFT_GIVEN_EVENT
from ..transform_timestamp_model import TRANSFORM_EVENT
from ..recipient_counter_occurrences import RECIPIENT_COUNTER_EVENT
from ..unblocked_attacker_model import UNBLOCKED_PLAYER_ATTACK_EVENT


class FixedCounterTriggerEvent(str, Enum):
    """Closed normalized event families accepted by this compiler slice."""

    STEP_BEGIN = "step.begin"
    DAY_NIGHT_TOGGLE = DAY_TOGGLE_EVENT
    DUNGEON_COMPLETED = DUNGEON_COMPLETED_EVENT
    CONTROLLER_BECOMES_MONARCH = MONARCH_BECAME_EVENT
    CREATURE_EXPLORES = 'creature.explores'
    SOURCE_MUTATED = 'permanent.mutated.self'
    PLAYER_COMMITTED_CRIME = 'player.committed_crime'
    TOKEN_CREATED = 'token.created'
    TOKENS_CREATED = 'tokens.created'
    SPELL_MANA_PAID = 'spell.mana_paid'
    CONTROLLED_LAND_ENTER = "land.enter"
    PUBLIC_LAND_PLAY = LAND_PLAY_EVENT
    LAND_MANA_TAP = LAND_MANA_TAP_EVENT
    SOURCE_MANA_TAP = SOURCE_MANA_TAP_EVENT
    CONTROLLER_SPELL_CAST = "spell.cast"
    SOURCE_ATTACKS = "creature.attacks"
    PLAYER_ATTACKS = PLAYER_ATTACK_DECLARATION
    DIRECT_PLAYER_ATTACK = DIRECT_PLAYER_ATTACK_EVENT
    PLAYER_OR_WALKER_ATTACKED = ATTACKED_PLAYER_OR_WALKER_EVENT
    ATTACKER_UNBLOCKED = 'creature.unblocked'
    PLAYER_ATTACKED_UNBLOCKED = UNBLOCKED_PLAYER_ATTACK_EVENT
    CONTROLLER_LIFE_GAIN = "life.gained"
    OPPONENT_LIFE_LOSS = LIFE_LOSS_EVENT
    OPPONENT_FIRST_OWN_TURN_LIFE_LOSS = FIRST_OWN_TURN_LIFE_LOSS_EVENT
    CONTROLLER_FIRST_LIFE_GAIN = FIRST_LIFE_GAIN_EVENT
    CONTROLLER_CARD_DRAW = "card.drawn"
    CONTROLLER_SECOND_DRAW = "card.second_draw"
    PERMANENT_ENTER = "permanent.enter"
    COMMITTED_NONLAND_MILL = MILL_EVENT
    ROOM_DOOR_UNLOCKED = DOOR_UNLOCKED_EVENT
    FINAL_SAGA_CHAPTER_RESOLVED = FINAL_SAGA_CHAPTER_RESOLVED_EVENT
    PERMANENT_TRANSFORMS = TRANSFORM_EVENT
    ARTIFACT_ENTER = "artifact.enter"
    CREATURE_ENTER = "creature.enter"
    ENCHANTMENT_ENTER = "enchantment.enter"
    CREATURE_DIES = "creature.dies"
    OPPONENT_LIBRARY_EXILE_BATCH = OPPONENT_LIBRARY_EXILE_EVENT
    PERMANENT_GRAVEYARD = "permanent.graveyard"
    SOURCE_VEHICLE_ENTER = "permanent.enter.self"
    SOURCE_VEHICLE_DIES = "creature.dies.self"
    SOURCE_GRAVEYARD = "permanent.graveyard.self"
    SOURCE_DEALS_DAMAGE = "damage.dealt.self"
    SOURCE_DEALT_DAMAGE = "damage.received"
    SOURCE_RECEIVED_COMBAT_DAMAGE = "combat.damage.received"
    ARTIFACT_GRAVEYARD = "artifact.graveyard"
    PUBLIC_DEALS_DAMAGE = "damage.dealt"
    CONTROLLED_SUBTYPE_DAMAGE_OPPONENTS = "damage.dealt.controlled_subtype_opponents"
    CONTROLLED_SUBTYPE_COMBAT_DAMAGE_PLAYER = "damage.dealt.controlled_subtype_combat_player"
    PUBLIC_ATTACKS = "creature.attacks"
    CREATURE_BLOCKS = "creature.blocks"
    CREATURE_BECOMES_BLOCKED = "creature.becomes_blocked"
    CREATURE_BECOMES_BLOCKED_BY = 'creature.becomes_blocked_by'
    CARD_CYCLED = "card.cycled"
    NINJUTSU_ACTIVATED = "ability.ninjutsu.activated"
    CREATURE_ACTIVATED = CREATURE_ABILITY_ACTIVATED
    GIFT_GIVEN = GIFT_GIVEN_EVENT
    NONMANA_ACTIVATED = ABILITY_ACTIVATION_COPY_EVENT
    SOURCE_CYCLED = "card.cycled.self"
    OWNED_ZONE_EXILE = OWNED_ZONE_EXILE_EVENT
    LIBRARY_CREATURE_ARRIVAL = "card.library_creature_graveyard_arrival.v1"
    CARD_LEAVE_GRAVEYARD = "card.leave_graveyard"
    CARD_LEAVE_GRAVEYARD_LOOKBACK = GRAVEYARD_DEPARTURE_LOOKBACK_EVENT
    CARD_ENTER_GRAVEYARD = 'card.enter_graveyard'
    GRAVEYARD_SOURCE_ARRIVAL = 'object.enter_graveyard'
    CARD_DISCARDED = "card.discarded"
    CARD_DISCARDED_BATCH = DISCARD_BATCH_EVENT
    TARGET_ANNOUNCED = 'object.became_target'
    PERMANENT_PHASE_IN = PHASE_IN_EVENT
    PERMANENT_LEAVE = "permanent.leave"
    OPPONENT_LIBRARY_SHUFFLE = "library.shuffled"
    OPPONENT_LIBRARY_SEARCH = "library.searched"
    PERMANENT_SACRIFICED = "permanent.sacrificed"
    SOURCE_SACRIFICED = "permanent.sacrificed.self"
    PERMANENT_TURNED_FACE_UP = "permanent.turned_face_up"
    OPPONENT_CARD_DRAW = "card.drawn"
    SPELL_CAST_OR_COPY = "spell.cast_or_copy"
    CLASS_LEVEL_CHANGED = "permanent.class_level_changed.self"
    PERMANENT_TAPPED = "permanent.tap"
    PERMANENT_UNTAPPED = "permanent.untap"
    ATTACHMENT_UNATTACHED = UNATTACHMENT_EVENT
    COUNTER_PLACED = 'counter.put'
    SINGLE_COUNTER_PLACED = 'counter.single_put'
    RECIPIENT_COUNTER_PLACED = RECIPIENT_COUNTER_EVENT
    CREATURE_EXPLOITS = 'creature.exploits'
    SOURCE_EXPLOITS = 'creature.exploits.self'
    PLAYER_SCRIED = 'player.scried'
    PLAYER_SURVEILLED = 'player.surveilled'
    PLAYER_WATERBENT = 'player.waterbent'
    PLAYER_RING_TEMPTED = 'player.ring_tempted'
    PERMANENT_DESIGNATED_MONSTROUS = 'permanent.designated_monstrous'
    PERMANENT_DESIGNATED_RENOWNED = 'permanent.designated_renowned'
    SOURCE_DESIGNATED_MONSTROUS = 'permanent.designated_monstrous.self'
    SOURCE_DESIGNATED_RENOWNED = 'permanent.designated_renowned.self'
