from __future__ import annotations

"""Execution version for alternative-face offers and intrinsic lifecycles."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import StackItem


CURRENT_CAST_FACE_OFFERS_VERSION = 2
ADVENTURE_CONTEXT = "adventure_resolution"
OMEN_CONTEXT = "omen_resolution"
OMEN_COVERAGE = "intrinsic_omen_resolution_required"


@dataclass(frozen=True, slots=True)
class OmenResolution:
    face: str

    def __post_init__(self) -> None:
        if type(self.face) is not str or not self.face:
            raise ValueError("Omen resolution requires a named alternative face")

    def to_dict(self) -> dict[str, str | int]:
        return {"schema_version": 1, "face": self.face}

    @classmethod
    def from_dict(cls, value: object) -> OmenResolution:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "face"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
        ):
            raise ValueError("Omen resolution fields are malformed")
        return cls(value["face"])


def omen_resolution_binding(item: StackItem) -> OmenResolution | None:
    if OMEN_CONTEXT not in item.context:
        return None
    binding = OmenResolution.from_dict(item.context[OMEN_CONTEXT])
    if (
        item.kind not in {"spell", "spell_copy"}
        or not item.card_object_id
        or item.chosen_face != binding.face
        or item.context.get("copy_permanent_spell")
        or ADVENTURE_CONTEXT in item.context
    ):
        raise ValueError("Omen resolution differs from its bound spell face")
    return binding


def cast_face_offers_version(value):
    if value is not None and (type(value) is not int or value not in {1, 2}):
        raise ValueError('Unsupported alternative-face offer version')
    return value


def complete_cast_face_offers(state):
    return cast_face_offers_version(getattr(state,'cast_face_offers_version',None)) is not None
