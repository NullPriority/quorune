from __future__ import annotations

"""Fixed paid graveyard methods using the canonical selected-hand cost owner."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib

from ..card_program_faces import program_matches_face
from ..card_programs.admission import program_has_complete_card_program_admission
from ..additional_cost_vocabulary import DISCARD_ONE_COST
from ..object_predicate import ObjectQuerySpec
from ..util import stable_json
from .casting_additional_costs import FixedZoneChangeAdditionalCost


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
    if (card.owner != seat or card.zone != 'graveyard' or card.face_down or card.object_kind != 'card'
            or card.annotations.get('copy_overrides') is not None):
        return ()
    record=host.card_record(card)
    if record is None:return ()
    from ..semantic_runtime.casting_activation_metadata import default_self_zone_cast_permission_registry
    registry=default_self_zone_cast_permission_registry();found=[];components=None
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,active_zone='all',event=EVENT):
        descriptors=[row for row in program.handlers if row.get('handler_id')==HANDLER_ID]
        if not descriptors:continue
        if (not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program)
                or not program_matches_face(record,program,card)):
            continue
        if components is None:components=frozenset(host._effective_static_component_keys(card))
        if program.key not in components:continue
        found.extend(spec for row in descriptors for spec in registry.lower(row,None))
    return tuple(sorted(set(found),key=lambda spec:spec.discard_count))


def self_graveyard_discard_base_options(host, seat, card, printed: Sequence[Mapping], *,
        cast_without_mana, force_without_mana_cost, suppress_source_costs):
    if cast_without_mana or force_without_mana_cost or suppress_source_costs:return []
    return [spec.cost_option(option) for spec in compiled_self_graveyard_discard_specs(host,seat,card)
        for option in printed if option.get('id')=='normal']


def validate_self_graveyard_discard_option(host, seat, card, option, *, origin):
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
