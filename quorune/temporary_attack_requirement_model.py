"""Closed resolved attack-if-able rule on one battlefield incarnation."""
from collections.abc import Mapping
from dataclasses import dataclass
from .declaration_fragments import DeclarationRequirementTemplate

TEMPORARY_ATTACK_REQUIREMENT_KIND = 'attack_each_combat_this_turn'
TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY = 'combat.requirement.resolved_attack_this_turn'


@dataclass(frozen=True, slots=True)
class TemporaryAttackRequirementSpec:
    schema_version: int = 1
    kind: str = TEMPORARY_ATTACK_REQUIREMENT_KIND

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or self.kind != TEMPORARY_ATTACK_REQUIREMENT_KIND:
            raise ValueError('Resolved attack requirements have one exact version and kind')

    def to_dict(self): return {'schema_version': self.schema_version, 'kind': self.kind}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind'}:
            raise ValueError('Resolved attack requirement fields are closed')
        return cls(**value)

    def to_requirement(self):
        return DeclarationRequirementTemplate('intrinsic-attack-each-combat-if-able-v1', 'attack', 'attack_each_combat')
