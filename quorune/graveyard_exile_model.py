from __future__ import annotations

"""Closed graveyard redirections over current kind, type, origin and relation."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from .zone_object_qualifications import ZONE_OBJECT_QUALIFICATIONS_V1

GRAVEYARD_EXILE_HANDLER = 'replacement.zone.graveyard-exile.v1'
GRAVEYARD_EXILE_CAPABILITY = 'zone.change.qualified_graveyard_exile'
GRAVEYARD_EXILE_DEPENDENCIES = (GRAVEYARD_EXILE_CAPABILITY,'zone.change.destination_replacement')


class GraveyardExileScope(StrEnum):
    CARD_OR_TOKEN = 'card_or_token'
    OPPONENT_CARD = 'opponent_card'
    PERMANENT = 'permanent'
    OPPONENT_CREATURE_CARD = 'opponent_creature_card'
    INSTANT_OR_SORCERY_CARD = 'instant_or_sorcery_card'
    OPPONENT_CONTROLLED_CREATURE = 'opponent_controlled_creature'


@dataclass(frozen=True, slots=True)
class GraveyardExileSpec:
    scope: GraveyardExileScope
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.scope, GraveyardExileScope)):
            raise ValueError('Graveyard exile requires one closed version1 subject')

    def to_dict(self):
        return {'schema_version':1,'scope':self.scope.value}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version','scope'}:
            raise ValueError('Graveyard exile subject has unknown or missing fields')
        return cls(GraveyardExileScope(value['scope']),value['schema_version'])

    def descriptor(self):
        return {'handler_id':GRAVEYARD_EXILE_HANDLER,'schema_version':1,'event':'zone.change',
                'subject':self.to_dict(),'destination':'exile'}

    @classmethod
    def from_descriptor(cls,value):
        if (not isinstance(value, Mapping) or set(value) != {'handler_id','schema_version','event','subject','destination'}
                or value['handler_id'] != GRAVEYARD_EXILE_HANDLER or type(value['schema_version']) is not int
                or value['schema_version'] != 1 or value['event'] != 'zone.change' or value['destination'] != 'exile'):
            raise ValueError('Qualified graveyard exile requires its complete versioned descriptor')
        return cls.from_dict(value['subject'])

    def conditions(self,source_controller):
        if type(source_controller) is not str or not source_controller:
            raise ValueError('Graveyard exile requires its current source controller')
        result={'destination':{'eq':'graveyard'}}
        if self.scope is GraveyardExileScope.CARD_OR_TOKEN:
            result[ZONE_OBJECT_QUALIFICATIONS_V1]={'contains':'card_or_token'}
        elif self.scope is GraveyardExileScope.OPPONENT_CARD:
            result.update(object_kind={'eq':'card'},owner={'not_in':[source_controller]})
            result[ZONE_OBJECT_QUALIFICATIONS_V1]={'contains':'card'}
        elif self.scope is GraveyardExileScope.PERMANENT:
            result['origin']={'eq':'battlefield'}
            result[ZONE_OBJECT_QUALIFICATIONS_V1]={'contains':'permanent'}
        elif self.scope is GraveyardExileScope.OPPONENT_CREATURE_CARD:
            # Whole permanent scope: do not opt into the legacy card-component
            # projection for a token-carried merged permanent (CR730.3e).
            result[ZONE_OBJECT_QUALIFICATIONS_V1]={'contains_any':[
                'nontoken_creature_permanent','creature_card_not_battlefield']}
            result['owner']={'not_in':[source_controller]}
        elif self.scope is GraveyardExileScope.INSTANT_OR_SORCERY_CARD:
            result.update(object_kind={'eq':'card'},object_types={'contains_any':['instant','sorcery']})
            result[ZONE_OBJECT_QUALIFICATIONS_V1]={'contains':'card'}
        else:
            # On a battlefield departure, the canonical event chooser is the
            # affected permanent's controller, independently of its owner.
            result.update(origin={'eq':'battlefield'},object_types={'contains':'creature'},
                          chooser={'not_in':[source_controller]})
            result[ZONE_OBJECT_QUALIFICATIONS_V1]={'contains':'creature_permanent'}
        return result
