"""CR509.3g: one unblocked occurrence after blocker declarations finish."""
from dataclasses import dataclass

UNBLOCKED_ATTACKER_EVENT='creature.unblocked'
UNBLOCKED_ATTACKER_CAPABILITY='trigger.event.unblocked_attacker'
UNBLOCKED_ATTACKER_VARIANT='source_attacks_unblocked'
CURRENT_UNBLOCKED_ATTACKER_EVENT_VERSION = 2
UNBLOCKED_PLAYER_ATTACK_EVENT = "combat.player_attacked_unblocked"
UNBLOCKED_PLAYER_ATTACK_VARIANT = "opponent_attacks_controller_unblocked"
UNBLOCKED_ATTACKER_PLAYER = "$context.attacking_player"


def unblocked_player_attack_condition():
    return {"field": "defender", "op": "eq", "value": "$source.controller"}


def unblocked_source_control_effects(count):
    from .recipient_control_model import recipient_control_effect

    return (
        {"op": "draw", "player": UNBLOCKED_ATTACKER_PLAYER, "count": count, "private": True},
        recipient_control_effect(card="$source.zone_object", controller=UNBLOCKED_ATTACKER_PLAYER),
        {"op": "untap", "card": "$source.zone_object"},
    )


def unblocked_attacker_event_version(value):
    if value is not None and (type(value) is not int or value not in (1, CURRENT_UNBLOCKED_ATTACKER_EVENT_VERSION)):
        raise ValueError('Unsupported unblocked-attacker event version')
    return value


@dataclass(frozen=True,slots=True)
class UnblockedAttackerOccurrence:
    reference:str
    object_id:str
    logical_object_id:str
    controller:str
    defending_player:str

    def __post_init__(self):
        if any(type(v)is not str or not v for v in (self.reference,self.object_id,self.logical_object_id,self.controller,self.defending_player)):
            raise ValueError('Unblocked occurrence requires exact attacker, controller and defending-player identities')

    def semantic_context(self):
        return {'card':self.reference,'card_object_id':self.object_id,'card_object_identity':self.logical_object_id,
            'controller':self.controller,'defending_player':self.defending_player,'types':['creature']}



UNBLOCKED_DEFENDER_POISON_CAPABILITY = "counter.producer.fixed_unblocked_defender_poison"
UNBLOCKED_DEFENDER_POISON_MECHANIC = "fixed-unblocked-defender-poison"


def intrinsic_unblocked_condition():
    return {"all": [
        {"field":"card","op":"eq","value":"$source.ref"},
        {"field":"card_object_identity","op":"eq","value":"$source.logical_object_id"},
    ]}


def unblocked_defender_poison_effect():
    return {"op":"place_player_counters","subjects":"target","counter":"poison",
            "amount":1,"source":"$source","target":"$context.defending_player"}
