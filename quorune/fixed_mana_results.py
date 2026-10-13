from __future__ import annotations

"""Closed fixed mana results; output selection is owned separately."""

from dataclasses import dataclass

FIXED_MANA_RESULT_CAPABILITY = 'mana.activated.fixed_result'


@dataclass(frozen=True, slots=True)
class FixedManaResultSpec:
    kind: str
    amount: int = 0
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Fixed mana result schema version is unsupported')
        if type(self.amount) is not int or type(self.kind) is not str or self.kind not in {'damage_controller','skip_actor_untap'}:
            raise ValueError('Fixed mana result requires a closed kind and integer amount')
        if (self.kind == 'damage_controller' and self.amount <= 0
                or self.kind == 'skip_actor_untap' and self.amount != 0):
            raise ValueError('Fixed mana result amount does not match its kind')

    def to_dict(self):
        return {'schema_version':self.schema_version,'kind':self.kind,'amount':self.amount}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value) != {'schema_version','kind','amount'}:
            raise ValueError('Fixed mana result fields are closed')
        return cls(**value)

    def mode_effect(self):
        return ({'op':'damage_self','amount':self.amount} if self.kind == 'damage_controller'
                else {'op':'skip_actor_untap'})
