from __future__ import annotations

"""Typed optional attack Exert and its linked triggered result."""

from dataclasses import dataclass

EXERT_ATTACK_CAPABILITY = 'combat.attack.exert'
EXERT_UNTAP_CAPABILITY = 'untap.step.exert_player'
EXERT_ATTACK_HANDLER = 'ability.static.exert-as-attacks.v1'
EXERT_PLAYER_ANNOTATION = 'exert_next_untap_players'


@dataclass(frozen=True, slots=True)
class ExertAttackSpec:
    result_semantic_key: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.result_semantic_key) is not str or not self.result_semantic_key
                or self.result_semantic_key != self.result_semantic_key.strip()):
            raise ValueError('Exert attack requires one typed linked program identity')

    def to_dict(self):
        return {'schema_version':1, 'result_semantic_key':self.result_semantic_key}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value) != {'schema_version','result_semantic_key'}:
            raise ValueError('Exert attack fields are closed')
        return cls(**value)
