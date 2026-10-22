from __future__ import annotations

"""Immutable lifetime facts for an effect ending at a specific unattachment."""

from dataclasses import dataclass, asdict
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AttachmentDuration:
    source_id: str
    source_incarnation: str
    target_id: str
    target_incarnation: str
    attachment_timestamp: int

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (
            self.source_id, self.source_incarnation, self.target_id, self.target_incarnation
        )) or self.source_id == self.target_id:
            raise ValueError('Attachment duration requires two distinct incarnations')
        if type(self.attachment_timestamp) is not int or self.attachment_timestamp < 0:
            raise ValueError('Attachment duration requires a nonnegative timestamp')

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AttachmentDuration:
        if not isinstance(value, Mapping) or set(value) != {
            'source_id', 'source_incarnation', 'target_id', 'target_incarnation', 'attachment_timestamp'
        }:
            raise ValueError('Attachment duration uses a closed schema')
        return cls(**value)

    def is_current(self, cards: Mapping[str, Any]) -> bool:
        source, target = cards.get(self.source_id), cards.get(self.target_id)
        return bool(source is not None and target is not None
            and source.zone == target.zone == 'battlefield'
            and source.logical_object_id == self.source_incarnation
            and target.logical_object_id == self.target_incarnation
            and source.attached_to == target.object_id
            and source.object_id in target.attachments
            and source.zone_timestamp == self.attachment_timestamp)


def validate_attachment_duration(effect: Any, locked: tuple[Any, ...]) -> None:
    from .continuous_effect_model import (
        ContinuousEffectDuration, ContinuousEffectOrigin, ContinuousObjectIdentity,
    )
    facts = effect.attachment_duration
    if effect.duration is ContinuousEffectDuration.UNTIL_UNATTACHED:
        if (not isinstance(facts, AttachmentDuration)
                or effect.origin is not ContinuousEffectOrigin.RESOLUTION
                or effect.source_id != facts.source_id
                or locked != (ContinuousObjectIdentity(facts.source_id, facts.source_incarnation),)):
            raise ValueError('Unattachment duration requires its original attached source')
    elif facts is not None:
        raise ValueError('Only unattachment duration may carry attachment facts')
