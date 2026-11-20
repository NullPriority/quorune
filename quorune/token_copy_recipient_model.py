"""Distinct announcement slots for a token creator and its copied creature."""
from collections.abc import Mapping
from dataclasses import dataclass

COPY_RECIPIENT_FIELD = 'copy_recipient_binding'
COPY_RECIPIENT_CAPABILITY = 'token.creation.target_player_copy'
COPY_RECIPIENT_MECHANIC = 'target-player-copy-creation'


@dataclass(frozen=True, slots=True)
class TokenCopyRecipientBinding:
    schema_version: int = 1
    recipient_index: int = 0
    copy_index: int = 1

    def __post_init__(self):
        if (any(type(v) is not int for v in (self.schema_version,self.recipient_index,self.copy_index))
                or (self.schema_version,self.recipient_index,self.copy_index)!=(1,0,1)):
            raise ValueError('Target-player copy requires its original recipient/source slots')

    def to_dict(self):
        return {'schema_version':1,'recipient_index':0,'copy_index':1}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','recipient_index','copy_index'}:
            raise ValueError('Token copy recipient slots have a closed schema')
        return cls(**value)
