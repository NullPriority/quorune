from __future__ import annotations

"""Typed ordinary fixed-mana face-down casting method contracts.

The represented family is deliberately narrower than the aggregate mechanics:
one printed Morph, Megamorph, or Disguise ability with a fixed ordinary mana
turn-up cost. Variable, hybrid, Phyrexian, snow, nonmana, copied, granted, and
other face-down methods remain outside this contract.
"""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping, Protocol

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .ability_fragments import ability_fragment_to_dict
from .keyword_abilities import normalized_effective_keywords
from .trigger_participation import WardSpec
from .util import mana_cost_to_vector, stable_json


MORPH_CAPABILITY_ID = "casting.morph.fixed_mana"
MEGAMORPH_CAPABILITY_ID = "casting.megamorph.fixed_mana"
DISGUISE_CAPABILITY_ID = "casting.disguise.fixed_mana"
MORPH_HANDLER_ID = "casting.morph.fixed-mana.v1"
MEGAMORPH_HANDLER_ID = "casting.megamorph.fixed-mana.v1"
DISGUISE_HANDLER_ID = "casting.disguise.fixed-mana.v1"
MORPH_RUNTIME_EVENT = "morph.action"
MEGAMORPH_RUNTIME_EVENT = "megamorph.action"
DISGUISE_RUNTIME_EVENT = "disguise.action"
MORPH_FACE_DOWN_ANNOTATION = "face_down_characteristics"
MORPH_METHOD_ANNOTATION = "face_down_method"
MORPH_CAST_METHOD = "morph"
MEGAMORPH_CAST_METHOD = "megamorph"
DISGUISE_CAST_METHOD = "disguise"
FACE_DOWN_CAST_METHODS = (
    MORPH_CAST_METHOD,
    MEGAMORPH_CAST_METHOD,
    DISGUISE_CAST_METHOD,
)
FACE_DOWN_METHOD_CAPABILITY_IDS = {
    MORPH_CAST_METHOD: MORPH_CAPABILITY_ID,
    MEGAMORPH_CAST_METHOD: MEGAMORPH_CAPABILITY_ID,
    DISGUISE_CAST_METHOD: DISGUISE_CAPABILITY_ID,
}
FACE_DOWN_METHOD_HANDLER_IDS = {
    MORPH_CAST_METHOD: MORPH_HANDLER_ID,
    MEGAMORPH_CAST_METHOD: MEGAMORPH_HANDLER_ID,
    DISGUISE_CAST_METHOD: DISGUISE_HANDLER_ID,
}
FACE_DOWN_METHOD_RUNTIME_EVENTS = {
    MORPH_CAST_METHOD: MORPH_RUNTIME_EVENT,
    MEGAMORPH_CAST_METHOD: MEGAMORPH_RUNTIME_EVENT,
    DISGUISE_CAST_METHOD: DISGUISE_RUNTIME_EVENT,
}
MORPH_FACE_DOWN_LABEL = "Face-down spell"
MORPH_FACE_DOWN_VALUES: dict[str, Any] = {
    "name": "",
    "mana_cost": "",
    "mana_value": 0,
    "text": "",
    "supertypes": [],
    "card_types": ["Creature"],
    "subtypes": [],
    "colors": [],
    "abilities": [],
    "power": 2,
    "toughness": 2,
}
_FACE_DOWN_METHOD_LINE = re.compile(
    r"^(?P<method>Morph|Megamorph|Disguise)\s+"
    r"(?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\.?$",
    re.IGNORECASE,
)
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")


class MorphError(ValueError):
    """A represented face-down descriptor, marker, or action is malformed."""


@dataclass(frozen=True, slots=True)
class FixedManaFaceDownMethodSpec:
    requirements: tuple[int, int, int, int, int, int, int]
    method: str = MORPH_CAST_METHOD
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise MorphError("Unsupported fixed-mana face-down schema version")
        if self.method not in FACE_DOWN_CAST_METHODS:
            raise MorphError("Unsupported fixed-mana face-down method")
        if (
            not isinstance(self.requirements, tuple)
            or len(self.requirements) != len(_MANA_FIELDS)
            or any(type(value) is not int or value < 0 for value in self.requirements)
        ):
            raise MorphError(
                "Fixed-mana face-down requirements must be seven nonnegative integers"
            )

    @classmethod
    def from_cost(
        cls,
        cost: str,
        *,
        method: str = MORPH_CAST_METHOD,
    ) -> "FixedManaFaceDownMethodSpec":
        requirements, complex_symbols = mana_cost_to_vector(cost)
        if complex_symbols:
            raise MorphError(
                "Face-down cost is outside the fixed ordinary-mana family"
            )
        return cls(
            tuple(int(requirements[field]) for field in _MANA_FIELDS),
            method=method,
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "FixedManaFaceDownMethodSpec":
        if not isinstance(value, Mapping) or frozenset(value) not in {
            frozenset({"schema_version", "requirements"}),
            frozenset({"schema_version", "method", "requirements"}),
        }:
            raise MorphError(
                "Fixed-mana face-down descriptors have a closed shape"
            )
        requirements = value["requirements"]
        if not isinstance(requirements, Mapping) or set(requirements) != set(
            _MANA_FIELDS
        ):
            raise MorphError(
                "Fixed-mana face-down requirements have a closed shape"
            )
        return cls(
            tuple(requirements[field] for field in _MANA_FIELDS),
            method=value.get("method", MORPH_CAST_METHOD),
            schema_version=value["schema_version"],
        )

    @property
    def requirements_dict(self) -> dict[str, int]:
        return dict(zip(_MANA_FIELDS, self.requirements, strict=True))

    @property
    def cost_text(self) -> str:
        parts: list[str] = []
        generic = self.requirements_dict["GENERIC"]
        if generic:
            parts.append(f"{{{generic}}}")
        for color in "WUBRGC":
            parts.extend(f"{{{color}}}" for _ in range(self.requirements_dict[color]))
        return "".join(parts) or "{0}"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(stable_json(self.to_dict()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "requirements": self.requirements_dict,
        }
        if self.method != MORPH_CAST_METHOD:
            result["method"] = self.method
        return result


FixedManaMorphSpec = FixedManaFaceDownMethodSpec


def compile_fixed_mana_face_down_method(
    material_line: str,
) -> FixedManaFaceDownMethodSpec | None:
    match = _FACE_DOWN_METHOD_LINE.fullmatch(material_line.strip())
    if match is None:
        return None
    method = match.group("method").casefold()
    try:
        return FixedManaFaceDownMethodSpec.from_cost(
            match.group("cost"),
            method=method,
        )
    except MorphError:
        return None


def compile_fixed_mana_morph(
    material_line: str,
) -> FixedManaFaceDownMethodSpec | None:
    spec = compile_fixed_mana_face_down_method(material_line)
    return spec if spec is not None and spec.method == MORPH_CAST_METHOD else None


def face_down_method_handler_descriptor(
    spec: FixedManaFaceDownMethodSpec,
) -> dict[str, Any]:
    payload_field = (
        "morph"
        if spec.method == MORPH_CAST_METHOD
        else "face_down_method"
    )
    return {
        "handler_id": FACE_DOWN_METHOD_HANDLER_IDS[spec.method],
        "schema_version": 1,
        "event": FACE_DOWN_METHOD_RUNTIME_EVENTS[spec.method],
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        payload_field: spec.to_dict(),
    }


def morph_handler_descriptor(
    spec: FixedManaFaceDownMethodSpec,
) -> dict[str, Any]:
    return face_down_method_handler_descriptor(spec)


def face_down_characteristics(
    method: str,
) -> dict[str, Any]:
    if method not in FACE_DOWN_CAST_METHODS:
        raise MorphError("Unsupported face-down characteristic method")
    values = {
        key: list(value) if isinstance(value, list) else value
        for key, value in MORPH_FACE_DOWN_VALUES.items()
    }
    if method == DISGUISE_CAST_METHOD:
        values["abilities"] = ["Ward {2}"]
        values["ability_fragments"] = [
            ability_fragment_to_dict(WardSpec(generic_cost=2))
        ]
    return values


def morph_face_down_annotation(
    spec: FixedManaFaceDownMethodSpec,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": spec.method,
        "spec_fingerprint": spec.fingerprint,
    }


def validate_morph_face_down_state(
    card: Any,
    spec: FixedManaFaceDownMethodSpec,
) -> None:
    marker = getattr(card, "annotations", {}).get(MORPH_METHOD_ANNOTATION)
    if (
        not getattr(card, "face_down", False)
        or not isinstance(marker, Mapping)
        or set(marker) != {"schema_version", "kind", "spec_fingerprint"}
        or marker.get("schema_version") != 1
        or marker.get("kind") != spec.method
        or marker.get("spec_fingerprint") != spec.fingerprint
        or getattr(card, "annotations", {}).get(MORPH_FACE_DOWN_ANNOTATION)
        != face_down_characteristics(spec.method)
    ):
        raise MorphError(
            "Face-down permanent is not the represented method object"
        )


class MorphCharacteristicHost(Protocol):
    def _effective_card_data(
        self,
        card: Any,
        *,
        ignore_face_down: bool = False,
    ) -> Mapping[str, Any]: ...


def current_face_up_has_face_down_method(
    host: MorphCharacteristicHost,
    card: Any,
    *,
    method: str,
) -> bool:
    """Use one shared effective-keyword boundary for layer-6 applicability."""

    if method not in FACE_DOWN_CAST_METHODS:
        raise MorphError("Unsupported current face-down method query")
    return method in normalized_effective_keywords(
        host,
        card,
        ignore_face_down=True,
    )


def current_face_up_has_morph(
    host: MorphCharacteristicHost,
    card: Any,
) -> bool:
    return current_face_up_has_face_down_method(
        host,
        card,
        method=MORPH_CAST_METHOD,
    )


__all__ = [
    "compile_fixed_mana_morph",
    "compile_fixed_mana_face_down_method",
    "current_face_up_has_face_down_method",
    "current_face_up_has_morph",
    "DISGUISE_CAPABILITY_ID",
    "DISGUISE_CAST_METHOD",
    "DISGUISE_HANDLER_ID",
    "DISGUISE_RUNTIME_EVENT",
    "FACE_DOWN_CAST_METHODS",
    "FACE_DOWN_METHOD_CAPABILITY_IDS",
    "FACE_DOWN_METHOD_HANDLER_IDS",
    "FACE_DOWN_METHOD_RUNTIME_EVENTS",
    "face_down_characteristics",
    "face_down_method_handler_descriptor",
    "FixedManaFaceDownMethodSpec",
    "FixedManaMorphSpec",
    "MEGAMORPH_CAPABILITY_ID",
    "MEGAMORPH_CAST_METHOD",
    "MEGAMORPH_HANDLER_ID",
    "MEGAMORPH_RUNTIME_EVENT",
    "MORPH_CAPABILITY_ID",
    "MORPH_CAST_METHOD",
    "MORPH_FACE_DOWN_ANNOTATION",
    "MORPH_FACE_DOWN_LABEL",
    "MORPH_FACE_DOWN_VALUES",
    "MORPH_HANDLER_ID",
    "MORPH_METHOD_ANNOTATION",
    "MORPH_RUNTIME_EVENT",
    "MorphError",
    "morph_face_down_annotation",
    "morph_handler_descriptor",
    "validate_morph_face_down_state",
]
