from __future__ import annotations

"""One resolved exile replacement bound to a battlefield incarnation."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError, ContinuousObjectIdentity


LINKED_DEPARTURE_EXILE_KIND = "resolution_linked_departure_exile"


@dataclass(frozen=True, slots=True)
class ResolvedLinkedDepartureExile:
    effect_id: str
    source_id: str
    subject: ContinuousObjectIdentity
    duration: ClassVar[ContinuousEffectDuration] = ContinuousEffectDuration.ZONE_OBJECT

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value for value in (self.effect_id, self.source_id))
                or not isinstance(self.subject, ContinuousObjectIdentity)):
            raise ContinuousEffectError("Linked departure exile requires its source and one typed incarnation")

    def to_dict(self) -> dict:
        return {"effect_kind": LINKED_DEPARTURE_EXILE_KIND, "schema_version": 1,
                "effect_id": self.effect_id, "source_id": self.source_id,
                "subject": self.subject.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping) -> ResolvedLinkedDepartureExile:
        if (not isinstance(value, Mapping) or set(value) != {
                "effect_kind", "schema_version", "effect_id", "source_id", "subject",
            } or value["effect_kind"] != LINKED_DEPARTURE_EXILE_KIND
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ContinuousEffectError("Linked departure exile journal fields are closed")
        return cls(value["effect_id"], value["source_id"],
                   ContinuousObjectIdentity.from_dict(value["subject"]))
