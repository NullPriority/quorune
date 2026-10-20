from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any, Mapping, TypeAlias

from .target_forms import TargetCharacteristicForm
from .target_numeric import (
    TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison,
)
from .util import stable_json


class AuraRuleError(ValueError):
    """A represented Aura rule value is malformed or unsupported."""


class AuraControllerRelation(str, Enum):
    ANY = "any"
    YOU = "you"
    OPPONENT = "opponent"


class AuraEnchantSubject(str, Enum):
    """The public rules domain constrained by one Enchant ability."""

    PERMANENT = "permanent"
    PLAYER = "player"
    GRAVEYARD_CARD = "graveyard_card"


_OBJECT_KINDS = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "land",
        "planeswalker",
        "permanent",
        "nonland permanent",
        "artifact or creature",
        "red or green creature",
        "tapped creature",
    }
)


@dataclass(frozen=True, slots=True)
class SimpleEnchantSpec:
    """Closed CR 702.5 object grammar supported by the Aura subsystem."""

    object_kind: str
    controller_relation: AuraControllerRelation = AuraControllerRelation.ANY
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AuraRuleError("Unsupported simple Enchant schema version")
        if not isinstance(self.object_kind, str) or not self.object_kind.strip():
            raise AuraRuleError(
                "Simple Enchant object kind must be a nonempty string"
            )
        normalized = " ".join(self.object_kind.casefold().split())
        if normalized not in _OBJECT_KINDS:
            raise AuraRuleError(
                f"Unsupported simple Enchant object kind: {self.object_kind!r}"
            )
        object.__setattr__(self, "object_kind", normalized)
        if not isinstance(self.controller_relation, AuraControllerRelation):
            raise AuraRuleError("Unsupported Enchant controller relation")

    def target_schema(self, source: Any | None = None) -> dict[str, Any]:
        del source
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "controller": self.controller_relation.value,
            "count": 1,
            "source_exclusion": True,
        }
        if self.object_kind == "artifact or creature":
            schema["types_any"] = ["artifact", "creature"]
        elif self.object_kind == "red or green creature":
            schema.update(
                {"types_all": ["creature"], "colors_any": ["R", "G"]}
            )
        elif self.object_kind == "tapped creature":
            schema.update({"types_all": ["creature"], "tapped": True})
        elif self.object_kind == "permanent":
            schema["permanent"] = True
        elif self.object_kind == "nonland permanent":
            schema.update({"permanent": True, "land": False})
        else:
            schema["types_all"] = [self.object_kind]
        return schema

    def linked_target_object_id(self, source: Any) -> str | None:
        del source
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "object_kind": self.object_kind,
            "controller_relation": self.controller_relation.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SimpleEnchantSpec":
        expected = {
            "schema_version",
            "object_kind",
            "controller_relation",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            missing = (
                sorted(expected - set(value))
                if isinstance(value, Mapping)
                else sorted(expected)
            )
            unknown = (
                sorted(set(value) - expected)
                if isinstance(value, Mapping)
                else []
            )
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unknown " + ", ".join(unknown))
            raise AuraRuleError(
                "Malformed simple Enchant value: " + "; ".join(details)
            )
        if type(value["schema_version"]) is not int:
            raise AuraRuleError("Simple Enchant schema version must be an integer")
        if not isinstance(value["object_kind"], str):
            raise AuraRuleError("Simple Enchant object kind must be a string")
        if not isinstance(value["controller_relation"], str):
            raise AuraRuleError(
                "Simple Enchant controller relation must be a string"
            )
        try:
            relation = AuraControllerRelation(value["controller_relation"])
        except ValueError as exc:
            raise AuraRuleError(
                "Unsupported Enchant controller relation"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            object_kind=value["object_kind"],
            controller_relation=relation,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


def _normalized_predicate_values(
    values: tuple[str, ...],
    *,
    field_name: str,
    uppercase: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or any(
        type(value) is not str or not value.strip() for value in values
    ):
        raise AuraRuleError(
            f"Typed Enchant {field_name} must be an array of nonempty strings"
        )
    normalized = tuple(
        sorted(
            value.strip().upper() if uppercase else value.strip().casefold()
            for value in values
        )
    )
    if len(normalized) != len(set(normalized)):
        raise AuraRuleError(
            f"Typed Enchant {field_name} must contain unique values"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class TypedEnchantSpec:
    """Closed typed CR 702.5 restriction over one public target domain."""

    subject: AuraEnchantSubject
    controller_relation: AuraControllerRelation = AuraControllerRelation.ANY
    player_relation: AuraControllerRelation = AuraControllerRelation.ANY
    types_any: tuple[str, ...] = ()
    types_all: tuple[str, ...] = ()
    types_none: tuple[str, ...] = ()
    subtypes_any: tuple[str, ...] = ()
    subtypes_none: tuple[str, ...] = ()
    supertypes_any: tuple[str, ...] = ()
    colors_any: tuple[str, ...] = ()
    colors_none: tuple[str, ...] = ()
    commander: bool | None = None
    characteristic_forms_any: tuple[TargetCharacteristicForm, ...] = ()
    schema_version: int = 2
    numeric_characteristic: TargetNumericCharacteristicSpec | None = None
    mana_value_max: int | None = None
    keywords_none: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {2, 3}:
            raise AuraRuleError("Unsupported typed Enchant schema version")
        self._validate_fixed_characteristic()
        if not isinstance(self.subject, AuraEnchantSubject):
            raise AuraRuleError("Unsupported typed Enchant subject")
        if not isinstance(self.controller_relation, AuraControllerRelation):
            raise AuraRuleError("Unsupported Enchant controller relation")
        if not isinstance(self.player_relation, AuraControllerRelation):
            raise AuraRuleError("Unsupported Enchant player relation")
        for field_name in (
            "types_any",
            "types_all",
            "types_none",
            "subtypes_any",
            "subtypes_none",
            "supertypes_any",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalized_predicate_values(
                    getattr(self, field_name),
                    field_name=field_name,
                ),
            )
        for field_name in ("colors_any", "colors_none"):
            values = _normalized_predicate_values(
                getattr(self, field_name),
                field_name=field_name,
                uppercase=True,
            )
            if set(values) - {"W", "U", "B", "R", "G"}:
                raise AuraRuleError(
                    f"Typed Enchant {field_name} contains an unsupported color"
                )
            object.__setattr__(self, field_name, values)
        if self.commander is not None and type(self.commander) is not bool:
            raise AuraRuleError(
                "Typed Enchant commander predicate must be boolean or null"
            )
        if not isinstance(self.characteristic_forms_any, (list, tuple)) or any(
            not isinstance(value, TargetCharacteristicForm)
            for value in self.characteristic_forms_any
        ):
            raise AuraRuleError(
                "Typed Enchant characteristic alternatives must be typed forms"
            )
        forms = tuple(self.characteristic_forms_any)
        if len(forms) != len(set(forms)):
            raise AuraRuleError(
                "Typed Enchant characteristic alternatives must be unique"
            )
        object.__setattr__(self, "characteristic_forms_any", forms)
        if set(self.types_all).intersection(self.types_none):
            raise AuraRuleError("Typed Enchant type predicates conflict")
        if set(self.subtypes_any).intersection(self.subtypes_none):
            raise AuraRuleError("Typed Enchant subtype predicates conflict")
        if set(self.colors_any).intersection(self.colors_none):
            raise AuraRuleError("Typed Enchant color predicates conflict")
        characteristic_fields = (
            self.types_any,
            self.types_all,
            self.types_none,
            self.subtypes_any,
            self.subtypes_none,
            self.supertypes_any,
            self.colors_any,
            self.colors_none,
            self.characteristic_forms_any,
        )
        if self.subject is AuraEnchantSubject.PLAYER:
            if any(characteristic_fields) or self.commander is not None:
                raise AuraRuleError(
                    "Player Enchant restrictions cannot carry object predicates"
                )
            if self.controller_relation is not AuraControllerRelation.ANY:
                raise AuraRuleError(
                    "Player Enchant restrictions use player_relation"
                )
        elif self.player_relation is not AuraControllerRelation.ANY:
            raise AuraRuleError(
                "Object Enchant restrictions cannot carry a player relation"
            )
        if (
            self.subject is AuraEnchantSubject.GRAVEYARD_CARD
            and self.controller_relation is not AuraControllerRelation.ANY
        ):
            raise AuraRuleError(
                "Graveyard-card Enchant restrictions do not use controller relation"
            )

    def _validate_fixed_characteristic(self) -> None:
        fields = (self.numeric_characteristic is not None,
            self.mana_value_max is not None, bool(self.keywords_none))
        if self.schema_version == 2:
            if any(fields) or self.keywords_none != ():
                raise AuraRuleError('Legacy typed Enchant cannot carry fixed characteristic fields')
            return
        if sum(fields) != 1 or self.subject is not AuraEnchantSubject.PERMANENT:
            raise AuraRuleError('Fixed Enchant requires one permanent characteristic restriction')
        if (self.types_all != ('creature',) or self.controller_relation is not AuraControllerRelation.ANY
                or self.player_relation is not AuraControllerRelation.ANY or self.commander is not None
                or any((self.types_any, self.types_none, self.subtypes_any, self.subtypes_none,
                    self.supertypes_any, self.colors_any, self.colors_none, self.characteristic_forms_any))):
            raise AuraRuleError('Fixed Enchant requires the closed creature domain')
        if self.numeric_characteristic is not None and (
                not isinstance(self.numeric_characteristic, TargetNumericCharacteristicSpec)
                or self.numeric_characteristic.characteristic is not TargetNumericCharacteristic.POWER
                or self.numeric_characteristic.comparison is not TargetNumericComparison.AT_MOST):
            raise AuraRuleError('Fixed Enchant supports only an existing maximum power predicate')
        if self.mana_value_max is not None and (type(self.mana_value_max) is not int or self.mana_value_max < 0):
            raise AuraRuleError('Enchant mana value maximum must be a nonnegative integer')
        if self.keywords_none not in ((), ('flying',)):
            raise AuraRuleError('Fixed Enchant keyword exclusion is outside the closed vocabulary')

    def linked_target_object_id(self, source: Any) -> str | None:
        del source
        return None

    def target_schema(self, source: Any | None = None) -> dict[str, Any]:
        del source
        if self.subject is AuraEnchantSubject.PLAYER:
            return {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": self.player_relation.value,
                "count": 1,
            }
        schema: dict[str, Any] = {
            "zones": [
                "graveyard"
                if self.subject is AuraEnchantSubject.GRAVEYARD_CARD
                else "battlefield"
            ],
            "categories": [
                "card"
                if self.subject is AuraEnchantSubject.GRAVEYARD_CARD
                else "permanent"
            ],
            "controller": self.controller_relation.value,
            "count": 1,
            "source_exclusion": True,
        }
        for field_name in (
            "types_any",
            "types_all",
            "types_none",
            "subtypes_any",
            "subtypes_none",
            "supertypes_any",
            "colors_any",
            "colors_none",
        ):
            values = getattr(self, field_name)
            if values:
                schema[field_name] = list(values)
        if self.commander is not None:
            schema["commander"] = self.commander
        if self.characteristic_forms_any:
            schema["characteristic_forms_any"] = [
                form.to_dict() for form in self.characteristic_forms_any
            ]
        if self.schema_version == 3:
            if self.numeric_characteristic is not None:
                schema['numeric_characteristic'] = self.numeric_characteristic.to_dict()
            if self.mana_value_max is not None:
                schema['mana_value_max'] = self.mana_value_max
            if self.keywords_none:
                schema['keywords_none'] = list(self.keywords_none)
        return schema

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "subject": self.subject.value,
            "controller_relation": self.controller_relation.value,
            "player_relation": self.player_relation.value,
            "types_any": list(self.types_any),
            "types_all": list(self.types_all),
            "types_none": list(self.types_none),
            "subtypes_any": list(self.subtypes_any),
            "subtypes_none": list(self.subtypes_none),
            "supertypes_any": list(self.supertypes_any),
            "colors_any": list(self.colors_any),
            "colors_none": list(self.colors_none),
            "commander": self.commander,
            "characteristic_forms_any": [
                form.to_dict() for form in self.characteristic_forms_any
            ],
        }
        if self.schema_version == 3:
            value.update(numeric_characteristic=(self.numeric_characteristic.to_dict()
                if self.numeric_characteristic is not None else None),
                mana_value_max=self.mana_value_max, keywords_none=list(self.keywords_none))
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TypedEnchantSpec":
        expected = {
            "schema_version",
            "subject",
            "controller_relation",
            "player_relation",
            "types_any",
            "types_all",
            "types_none",
            "subtypes_any",
            "subtypes_none",
            "supertypes_any",
            "colors_any",
            "colors_none",
            "commander",
            "characteristic_forms_any",
        }
        if isinstance(value, Mapping) and value.get('schema_version') == 3:
            expected |= {'numeric_characteristic', 'mana_value_max', 'keywords_none'}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise AuraRuleError("Typed Enchant values have a closed schema")
        if value['schema_version'] == 3 and not isinstance(value['keywords_none'], (list, tuple)):
            raise AuraRuleError('Typed Enchant keyword exclusions must be an array')
        array_fields = (
            "types_any",
            "types_all",
            "types_none",
            "subtypes_any",
            "subtypes_none",
            "supertypes_any",
            "colors_any",
            "colors_none",
            "characteristic_forms_any",
        )
        if any(
            not isinstance(value[field_name], (list, tuple))
            for field_name in array_fields
        ):
            raise AuraRuleError(
                "Typed Enchant predicate fields must be arrays"
            )
        try:
            subject = AuraEnchantSubject(value["subject"])
            controller_relation = AuraControllerRelation(
                value["controller_relation"]
            )
            player_relation = AuraControllerRelation(value["player_relation"])
            forms = tuple(
                TargetCharacteristicForm.from_mapping(form)
                for form in value["characteristic_forms_any"]
            )
        except (TypeError, ValueError) as exc:
            raise AuraRuleError("Malformed typed Enchant value") from exc
        return cls(
            schema_version=value["schema_version"],
            subject=subject,
            controller_relation=controller_relation,
            player_relation=player_relation,
            types_any=tuple(value["types_any"]),
            types_all=tuple(value["types_all"]),
            types_none=tuple(value["types_none"]),
            subtypes_any=tuple(value["subtypes_any"]),
            subtypes_none=tuple(value["subtypes_none"]),
            supertypes_any=tuple(value["supertypes_any"]),
            colors_any=tuple(value["colors_any"]),
            colors_none=tuple(value["colors_none"]),
            commander=value["commander"],
            characteristic_forms_any=forms,
            numeric_characteristic=(TargetNumericCharacteristicSpec.from_mapping(value['numeric_characteristic'])
                if value.get('numeric_characteristic') is not None else None),
            mana_value_max=value.get('mana_value_max'),
            keywords_none=tuple(value.get('keywords_none', ())),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class LinkedGraveyardCreatureEnchantSpec:
    """Closed linked Enchant restriction used by reanimating Auras.

    The initial restriction selects a creature card in a graveyard. Once the
    reviewed semantic instruction records the linked physical object, the
    restriction follows only that object on the battlefield. This models the
    rules-bearing transition without reparsing Oracle text or storing a raw
    target-schema override on the Aura.
    """

    link_annotation: str
    controller_relation: AuraControllerRelation = AuraControllerRelation.ANY
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AuraRuleError(
                "Unsupported linked Enchant schema version"
            )
        if (
            not isinstance(self.link_annotation, str)
            or not self.link_annotation.strip()
        ):
            raise AuraRuleError(
                "Linked Enchant requires a nonempty annotation key"
            )
        object.__setattr__(
            self,
            "link_annotation",
            self.link_annotation.strip(),
        )
        if not isinstance(self.controller_relation, AuraControllerRelation):
            raise AuraRuleError("Unsupported Enchant controller relation")

    def linked_target_object_id(self, source: Any) -> str | None:
        annotations = getattr(source, "annotations", None)
        if not isinstance(annotations, Mapping):
            return None
        value = annotations.get(self.link_annotation)
        return str(value) if isinstance(value, str) and value else None

    def target_schema(self, source: Any | None = None) -> dict[str, Any]:
        linked_id = self.linked_target_object_id(source)
        if linked_id is not None:
            return {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "controller": self.controller_relation.value,
                "creature": True,
                "count": 1,
                "source_exclusion": True,
            }
        return {
            "zones": ["graveyard"],
            "categories": ["card"],
            "controller": self.controller_relation.value,
            "creature": True,
            "count": 1,
            "source_exclusion": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "link_annotation": self.link_annotation,
            "controller_relation": self.controller_relation.value,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "LinkedGraveyardCreatureEnchantSpec":
        expected = {
            "schema_version",
            "link_annotation",
            "controller_relation",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise AuraRuleError(
                "Linked Enchant values require schema_version, "
                "link_annotation, and controller_relation"
            )
        if type(value["schema_version"]) is not int:
            raise AuraRuleError(
                "Linked Enchant schema version must be an integer"
            )
        if not isinstance(value["link_annotation"], str):
            raise AuraRuleError(
                "Linked Enchant annotation key must be a string"
            )
        if not isinstance(value["controller_relation"], str):
            raise AuraRuleError(
                "Linked Enchant controller relation must be a string"
            )
        try:
            relation = AuraControllerRelation(value["controller_relation"])
        except ValueError as exc:
            raise AuraRuleError(
                "Unsupported Enchant controller relation"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            link_annotation=value["link_annotation"],
            controller_relation=relation,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


EnchantSpec: TypeAlias = (
    SimpleEnchantSpec | TypedEnchantSpec | LinkedGraveyardCreatureEnchantSpec
)


def enchant_spec_to_dict(spec: EnchantSpec) -> dict[str, Any]:
    if isinstance(spec, SimpleEnchantSpec):
        kind = "simple_object"
    elif isinstance(spec, TypedEnchantSpec):
        kind = "typed_restriction"
    elif isinstance(spec, LinkedGraveyardCreatureEnchantSpec):
        kind = "linked_graveyard_creature"
    else:
        raise AuraRuleError(
            f"Unsupported Enchant spec {type(spec).__name__}"
        )
    return {"kind": kind, "value": spec.to_dict()}


def enchant_spec_from_dict(value: Mapping[str, Any]) -> EnchantSpec:
    if not isinstance(value, Mapping) or set(value) != {"kind", "value"}:
        raise AuraRuleError("Enchant specs require exactly kind and value")
    kind = value["kind"]
    raw = value["value"]
    if not isinstance(kind, str) or not isinstance(raw, Mapping):
        raise AuraRuleError(
            "Enchant spec kind must be a string and value an object"
        )
    if kind == "simple_object":
        return SimpleEnchantSpec.from_dict(raw)
    if kind == "typed_restriction":
        return TypedEnchantSpec.from_dict(raw)
    if kind == "linked_graveyard_creature":
        return LinkedGraveyardCreatureEnchantSpec.from_dict(raw)
    raise AuraRuleError(f"Unsupported Enchant spec kind {kind!r}")


__all__ = [
    "AuraControllerRelation",
    "AuraEnchantSubject",
    "AuraRuleError",
    "EnchantSpec",
    "LinkedGraveyardCreatureEnchantSpec",
    "SimpleEnchantSpec",
    "TypedEnchantSpec",
    "enchant_spec_from_dict",
    "enchant_spec_to_dict",
]
