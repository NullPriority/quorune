from __future__ import annotations

"""Closed return instructions for the Aura whose enchanted creature died."""

from dataclasses import dataclass
from typing import Mapping

OPERATION = 'reanimate'
RETURN_VERSION = 7
CAPABILITY = 'zone.return.attachment_departure_source'
MECHANIC = 'attachment-departure-source-return'
SOURCE_FIELD = 'attachment_departure_source'
SOURCE_REFERENCE = '$attachment_departure.source'


@dataclass(frozen=True, slots=True)
class AttachmentSourceReturnSpec:
    graveyard_scope: str
    timing: str
    transformed: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported attachment-source return version')
        if (type(self.graveyard_scope) is not str or type(self.timing) is not str
                or type(self.transformed) is not bool) or (
            self.graveyard_scope, self.timing, self.transformed
        ) not in {
            ('controller', 'immediate', False),
            ('any', 'next_end_step', False),
            ('any', 'immediate', True),
        }:
            raise ValueError('Unrepresented attachment-source return instruction')

    def to_dict(self):
        return {
            'schema_version': self.schema_version,
            'graveyard_scope': self.graveyard_scope,
            'timing': self.timing,
            'transformed': self.transformed,
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            'schema_version', 'graveyard_scope', 'timing', 'transformed'
        }:
            raise ValueError('Attachment-source returns have a closed schema')
        return cls(**value)

    def allows_graveyard(self, *, graveyard_owner: str, trigger_controller: str):
        if any(type(p) is not str or not p for p in (graveyard_owner, trigger_controller)):
            raise ValueError('A return requires its graveyard owner and trigger controller')
        return self.graveyard_scope == 'any' or graveyard_owner == trigger_controller

    def effect(self):
        return {'op': OPERATION, 'schema_version': RETURN_VERSION, 'attachment_return': self.to_dict(),
                'source': SOURCE_REFERENCE, 'phase': 'start'}

    @property
    def capabilities(self):
        return tuple(sorted({CAPABILITY, 'attachment.reference.current_or_lki',
            'zone.change.destination_replacement',
            *({'permanent.transform.face_change'} if self.transformed else {'attachment.aura.simple_object'}),
            *({'trigger.delayed.fixed_object_action'} if self.timing == 'next_end_step' else set())}))


@dataclass(frozen=True, slots=True)
class AttachmentDepartureSource:
    object_id: str
    reference: str
    logical_object_id: str
    battlefield_counter: int
    owner: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.battlefield_counter) is not int or self.battlefield_counter < 0
                or any(type(v) is not str or not v for v in
                       (self.object_id, self.reference, self.logical_object_id, self.owner))):
            raise ValueError('An attachment departure requires its original source incarnation')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'object_id': self.object_id,
                'reference': self.reference, 'logical_object_id': self.logical_object_id,
                'battlefield_counter': self.battlefield_counter, 'owner': self.owner}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            'schema_version', 'object_id', 'reference', 'logical_object_id', 'battlefield_counter', 'owner'
        }:
            raise ValueError('Attachment departure source fields are closed')
        return cls(**value)

    def matches_first_graveyard(self, card):
        return (card is not None and card.object_id == self.object_id and card.ref == self.reference
                and card.owner == self.owner and card.zone == 'graveyard' and not card.phased_out
                and card.object_kind == 'card' and card.zone_change_counter == self.battlefield_counter + 1)


def is_attachment_source_return(effect):
    return (isinstance(effect, Mapping) and effect.get('op') == OPERATION
            and ('attachment_return' in effect or effect.get('schema_version') == RETURN_VERSION))


def validate_attachment_source_return(effect, *, compiled=False):
    fields = {'op', 'schema_version', 'attachment_return', 'source', 'phase'}
    runtime = {'reason', '_runtime_source', '_aura_target_ref', '_replacement_selections'}
    if (not isinstance(effect, Mapping) or not fields <= set(effect)
            or set(effect) - fields - (set() if compiled else runtime)
            or effect['op'] != OPERATION or type(effect['schema_version']) is not int
            or effect['schema_version'] != RETURN_VERSION):
        raise ValueError('Attachment-source return instruction is malformed')
    spec = AttachmentSourceReturnSpec.from_dict(effect['attachment_return'])
    if effect['phase'] not in ('start', 'return') or (
            effect['phase'] == 'return' and spec.timing != 'next_end_step'):
        raise ValueError('Attachment-source return phase is unsupported')
    if compiled:
        if effect['source'] != SOURCE_REFERENCE or effect['phase'] != 'start':
            raise ValueError('Compiled attachment-source return requires the original death occurrence')
    else:
        AttachmentDepartureSource.from_dict(effect['source'])
        if '_aura_target_ref' in effect and (spec.transformed or
                type(effect['_aura_target_ref']) is not str or not effect['_aura_target_ref']):
            raise ValueError('Aura entry choice is invalid for this return')
        selections = effect.get('_replacement_selections', ())
        if not isinstance(selections, (list, tuple)):
            raise ValueError('Return replacements require a sequence')
    return spec


def compile_attachment_source_return_body(text):
    if type(text) is not str:
        return None
    body = ' '.join(text.strip().split()).casefold()
    if body == 'return this card from your graveyard to the battlefield.':
        return AttachmentSourceReturnSpec('controller', 'immediate')
    if body == 'return this card to the battlefield at the beginning of the next end step.':
        return AttachmentSourceReturnSpec('any', 'next_end_step')
    if body == 'return this card to the battlefield transformed under your control.':
        return AttachmentSourceReturnSpec('any', 'immediate', transformed=True)
    return None
