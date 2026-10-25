from __future__ import annotations

"""Fixed paid graveyard methods using the canonical selected-hand cost owner."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib

from ..additional_cost_vocabulary import DISCARD_ONE_COST
from ..object_predicate import ObjectQuerySpec
from ..util import stable_json
from .casting_additional_costs import FixedZoneChangeAdditionalCost
from .graveyard_payment_sources import compiled_graveyard_payment_specs


CAPABILITY = 'casting.zone.self_graveyard.fixed_discard'
HANDLER_ID = 'permission.cast.self-graveyard-discard.v1'
EVENT = 'cast.zone.permission'
CONTEXT_FIELD = 'self_graveyard_discard'
FINGERPRINT_FIELD = 'self_graveyard_discard_fingerprint'
CAPABILITIES = (CAPABILITY, 'casting.zone.self_public_repeatable', 'casting.additional_cost.zone_change.fixed_discard')


@dataclass(frozen=True, slots=True)
class SelfGraveyardDiscardSpec:
    discard_count: int
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported fixed-discard graveyard method version')
        if type(self.discard_count) is not int or self.discard_count < 1:
            raise ValueError('Graveyard discard payment requires a positive fixed card count')

    def to_dict(self):
        return {'schema_version':1, 'discard_count':self.discard_count}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version','discard_count'}:
            raise ValueError('Graveyard discard methods have closed typed fields')
        return cls(value['discard_count'], value['schema_version'])

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()

    @property
    def option_id(self):
        return f'graveyard-discard:{self.discard_count}'

    def discard_descriptor(self):
        return FixedZoneChangeAdditionalCost(operation=DISCARD_ONE_COST, choice_field='discard_cards',
            predicate=ObjectQuerySpec(zones=('hand',),owner='$actor',known_to_actor=True),
            count=self.discard_count).to_descriptor()

    def cost_option(self, printed):
        if printed.get('id') != 'normal':
            raise ValueError('Intrinsic graveyard discard method requires its printed mana base')
        option=deepcopy(dict(printed))
        option.update(id=self.option_id, kind='zone_cast',
            label=f'Cast from graveyard; discard {self.discard_count} '+('card' if self.discard_count==1 else 'cards'),
            source_zone='graveyard', base_cost_option='normal',
            **{CONTEXT_FIELD:self.to_dict(),FINGERPRINT_FIELD:self.fingerprint})
        option['_additional_option_costs']=[*option.get('_additional_option_costs',()),self.discard_descriptor()]
        return option


def compiled_self_graveyard_discard_specs(host, seat, card):
    return tuple(sorted(compiled_graveyard_payment_specs(host, seat, card, HANDLER_ID),
        key=lambda spec: spec.discard_count))


def self_graveyard_discard_base_options(host, seat, card, printed: Sequence[Mapping], *,
        cast_without_mana, force_without_mana_cost, suppress_source_costs):
    if cast_without_mana or force_without_mana_cost or suppress_source_costs:return []
    return [spec.cost_option(option) for spec in compiled_self_graveyard_discard_specs(host,seat,card)
        for option in printed if option.get('id')=='normal']


def validate_self_graveyard_discard_option(host, seat, card, option, *, origin):
    from .graveyard_cast_costs import validate_paid_graveyard_option
    validate_paid_graveyard_option(host, seat, card, option, origin=origin)
    raw=option.get(CONTEXT_FIELD)
    if raw is None:
        if str(option.get('id','')).startswith('graveyard-discard:'):
            raise ValueError('Graveyard discard method is missing its sealed cost contract')
        return
    spec=SelfGraveyardDiscardSpec.from_dict(raw)
    if (origin!='graveyard' or option.get('source_zone')!='graveyard' or option.get('id')!=spec.option_id
            or option.get(FINGERPRINT_FIELD)!=spec.fingerprint
            or spec not in compiled_self_graveyard_discard_specs(host,seat,card)
            or list(option.get('additional_costs',())).count(spec.discard_descriptor())!=1):
        raise ValueError('The intrinsic graveyard discard contract changed before payment')
