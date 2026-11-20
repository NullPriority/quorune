"""The affected player chooses one of their current graveyard cards to exile."""
from collections.abc import Mapping
from dataclasses import dataclass
from .graveyard_exile_result_model import GraveyardExileLifeResultSpec


GRAVEYARD_EXILE_CHOICE_OPERATION = 'choose_target_player_graveyard_exile'
GRAVEYARD_EXILE_CHOICE_MECHANIC = 'target-player-graveyard-exile-choice'
GRAVEYARD_EXILE_CHOICE_CAPABILITY = 'choice.affected_player.graveyard_exile'


@dataclass(frozen=True, slots=True)
class GraveyardExileChoiceSpec:
    schema_version: int = 1
    result: GraveyardExileLifeResultSpec | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError('Graveyard exile choice requires an explicit supported version')
        if (self.schema_version == 1 and self.result is not None
                or self.schema_version == 2 and not isinstance(self.result, GraveyardExileLifeResultSpec)):
            raise ValueError('Only graveyard choice version two owns a required typed card result')

    def to_dict(self):
        return {'kind': 'one_owned_graveyard_card', 'schema_version': self.schema_version,
            **({'result': self.result.to_dict()} if self.schema_version == 2 else {})}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Graveyard exile choice must be a typed descriptor')
        version = value.get('schema_version')
        expected = {'kind', 'schema_version'} | ({'result'} if type(version) is int and version == 2 else set())
        if (set(value) != expected or value.get('kind') != 'one_owned_graveyard_card'):
            raise ValueError('Graveyard exile choice descriptor fields are closed')
        result = GraveyardExileLifeResultSpec.from_dict(value['result']) if version == 2 else None
        return cls(version, result)

    def effect(self, *, player='$target.0'):
        if type(player) is not str or not player:
            raise ValueError('Graveyard exile requires its affected player')
        return {'op': GRAVEYARD_EXILE_CHOICE_OPERATION, 'player': player, 'selection': self.to_dict()}

    @classmethod
    def from_effect(cls, value, *, player='$target.0'):
        if (not isinstance(value, Mapping) or set(value) != {'op', 'player', 'selection'}
                or value.get('op') != GRAVEYARD_EXILE_CHOICE_OPERATION
                or type(player) is not str or not player or value.get('player') != player):
            raise ValueError('Graveyard exile instruction fields and affected player are closed')
        return cls.from_dict(value['selection'])
