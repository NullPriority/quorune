"""Closed normalized-event vocabulary shared by trigger binding consumers."""
from enum import Enum


class FixedCounterTriggerEvent(str, Enum):
    """Closed normalized event families accepted by this compiler slice."""

    STEP_BEGIN = "step.begin"
    SPELL_MANA_PAID = 'spell.mana_paid'
    CONTROLLED_LAND_ENTER = "land.enter"
    CONTROLLER_SPELL_CAST = "spell.cast"
    SOURCE_ATTACKS = "creature.attacks"
    CONTROLLER_LIFE_GAIN = "life.gained"
    CONTROLLER_CARD_DRAW = "card.drawn"
    CONTROLLER_SECOND_DRAW = "card.second_draw"
    PERMANENT_ENTER = "permanent.enter"
    ARTIFACT_ENTER = "artifact.enter"
    CREATURE_ENTER = "creature.enter"
    ENCHANTMENT_ENTER = "enchantment.enter"
    CREATURE_DIES = "creature.dies"
    PERMANENT_GRAVEYARD = "permanent.graveyard"
    SOURCE_VEHICLE_ENTER = "permanent.enter.self"
    SOURCE_VEHICLE_DIES = "creature.dies.self"
    SOURCE_GRAVEYARD = "permanent.graveyard.self"
    SOURCE_DEALS_DAMAGE = "damage.dealt.self"
    SOURCE_DEALT_DAMAGE = "damage.received"
    SOURCE_RECEIVED_COMBAT_DAMAGE = "combat.damage.received"
    ARTIFACT_GRAVEYARD = "artifact.graveyard"
    PUBLIC_DEALS_DAMAGE = "damage.dealt"
    PUBLIC_ATTACKS = "creature.attacks"
    CREATURE_BLOCKS = "creature.blocks"
    CREATURE_BECOMES_BLOCKED = "creature.becomes_blocked"
    CARD_CYCLED = "card.cycled"
    SOURCE_CYCLED = "card.cycled.self"
    CARD_LEAVE_GRAVEYARD = "card.leave_graveyard"
    CARD_ENTER_GRAVEYARD = 'card.enter_graveyard'
    CARD_DISCARDED = "card.discarded"
    TARGET_ANNOUNCED = 'object.became_target'
    PERMANENT_LEAVE = "permanent.leave"
    PERMANENT_SACRIFICED = "permanent.sacrificed"
    SOURCE_SACRIFICED = "permanent.sacrificed.self"
    PERMANENT_TURNED_FACE_UP = "permanent.turned_face_up"
    OPPONENT_CARD_DRAW = "card.drawn"
    SPELL_CAST_OR_COPY = "spell.cast_or_copy"
    CLASS_LEVEL_CHANGED = "permanent.class_level_changed.self"
    PERMANENT_TAPPED = "permanent.tap"
    PERMANENT_UNTAPPED = "permanent.untap"
    COUNTER_PLACED = 'counter.put'
    SINGLE_COUNTER_PLACED = 'counter.single_put'
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
