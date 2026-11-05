"""CR509.3g: one unblocked occurrence after blocker declarations finish."""
from dataclasses import dataclass

UNBLOCKED_ATTACKER_EVENT='creature.unblocked'
UNBLOCKED_ATTACKER_CAPABILITY='trigger.event.unblocked_attacker'
UNBLOCKED_ATTACKER_VARIANT='source_attacks_unblocked'


def unblocked_attacker_event_version(value):
    if value is not None and (type(value)is not int or value!=1):raise ValueError('Unsupported unblocked-attacker event version')
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
