"""The normal and alternative copiable values of a flip model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..replacement.immutable import FrozenMap, thaw_value


COPIED_FLIP_FACES = "copiable_flip_faces"
FLIPPED_STATUS = "flipped"
FLIP_SOURCE_OPERATION = "flip_source_permanent"
FLIP_SOURCE_CAPABILITY = "permanent.flip.original_source"
COUNTER_END_STEP_FLIP_MECHANIC = "optional-counter-end-step-source-flip"


@dataclass(frozen=True, slots=True)
class CopiedFlipFaces:
    normal: FrozenMap
    alternative: FrozenMap

    def __post_init__(self):
        if not isinstance(self.normal, Mapping) or not isinstance(self.alternative, Mapping):
            raise ValueError("A copied flip model requires its two public faces")
        if (any(type(face.get("name")) is not str or not face["name"] for face in (self.normal, self.alternative))
                or self.normal["name"] == self.alternative["name"]
                or any(self.normal.get(field) != self.alternative.get(field) for field in ("mana_cost", "colors"))):
            raise ValueError("Flip faces require distinct names and the same mana cost and colors")
        object.__setattr__(self, "normal", FrozenMap(self.normal))
        object.__setattr__(self, "alternative", FrozenMap(self.alternative))

    def to_dict(self):
        return {"schema_version": 1, "normal": thaw_value(self.normal), "alternative": thaw_value(self.alternative)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "normal", "alternative"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Copied flip faces require a closed version-one descriptor")
        return cls(value["normal"], value["alternative"])

    def face(self, *, flipped: bool) -> FrozenMap:
        if type(flipped) is not bool:
            raise ValueError("A copied flip face requires a typed receiver status")
        return self.alternative if flipped else self.normal

    def current_values(self, *, flipped: bool) -> dict:
        return {**thaw_value(self.face(flipped=flipped)), COPIED_FLIP_FACES: self.to_dict()}


def current_flip_copy_values(card, values):
    if COPIED_FLIP_FACES not in values:
        return values
    faces = CopiedFlipFaces.from_dict(values[COPIED_FLIP_FACES])
    flipped = card.annotations.get(FLIPPED_STATUS)
    if flipped is None:
        flipped = values.get("_copiable_face_id") == faces.alternative.get("_copiable_face_id")
    return {**thaw_value(faces.face(flipped=flipped)), COPIED_FLIP_FACES: values[COPIED_FLIP_FACES]}


def current_flipped_status(host, card) -> bool:
    value = card.annotations.get(FLIPPED_STATUS)
    if value is not None:
        if type(value) is not bool:
            raise ValueError("The retained flipped status must be a boolean")
        return value
    printed = host.card_db.by_oracle_id(card.oracle_id)
    return bool(printed is not None and printed.layout == "flip" and len(printed.faces) == 2
                and card.active_face == printed.faces[1].get("name"))


def capture_copied_flip_faces(host, model) -> CopiedFlipFaces | None:
    """Read both public copiable faces independently of the model's flip status."""
    from ..entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD

    if model.face_down:
        return None
    overrides = model.annotations.get("copy_overrides") or {}
    if COPIED_FLIP_FACES in overrides:
        return CopiedFlipFaces.from_dict(overrides[COPIED_FLIP_FACES])
    printed = host.card_record(model)
    if overrides or printed is None or printed.layout != "flip" or len(printed.faces) != 2:
        return None

    def capture(face):
        label = face["name"]
        view = replace(model, active_face=label, annotations={
            key: value for key, value in model.annotations.items() if key != FLIPPED_STATUS
        })
        values = dict(host._copyable_characteristics(view))
        return {**values, COPIABLE_ORACLE_FIELD: model.oracle_id, COPIABLE_FACE_FIELD: label}

    normal, alternative = (capture(face) for face in printed.faces)
    for field in ("mana_cost", "mana_value", "colors"):
        alternative[field] = normal[field]
    if "color_indicator" in normal:
        alternative["color_indicator"] = normal["color_indicator"]
    else:
        alternative.pop("color_indicator", None)
    return CopiedFlipFaces(normal, alternative)
