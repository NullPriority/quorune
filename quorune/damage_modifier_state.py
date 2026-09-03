from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, TypeAlias

from .damage_source import DamageError, DamageSourceSnapshot
from .object_predicate import (
    ObjectQueryError,
    ObjectQuerySpec,
    validate_chosen_damage_source_predicate,
)
from .prevention_triggers import (
    PreventionTriggeredAbility,
    PreventionTriggerError,
)


class DamageModifierError(ValueError):
    """A durable prevention or redirection value is malformed or stale."""


class PreventionMode(str, Enum):
    AMOUNT = "amount"
    NEXT_INSTANCE = "next_instance"
    ALL = "all"


class DamageModifierDuration(str, Enum):
    UNTIL_END_OF_TURN = "until_end_of_turn"
    UNTIL_USED = "until_used"


class PreventionDamageKind(str, Enum):
    ANY = "any"
    COMBAT = "combat"
    NONCOMBAT = "noncombat"


class PreventionRecipientKind(str, Enum):
    ANY = "any"
    PLAYER = "player"


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, label: str
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if unknown:
        details.append("unknown " + ", ".join(unknown))
    raise DamageModifierError(f"{label} fields: {'; '.join(details)}")


def _strings(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DamageModifierError(f"{label} must be an array")
    result = tuple(sorted({str(item) for item in value if str(item)}))
    if len(result) != len(value):
        raise DamageModifierError(
            f"{label} must contain unique nonempty strings"
        )
    return result


@dataclass(frozen=True, slots=True)
class DamageSubject:
    ref: str
    kind: str
    controller: str
    object_id: str | None = None
    logical_object_id: str | None = None
    owner: str | None = None

    def __post_init__(self) -> None:
        ref = str(self.ref or "")
        kind = str(self.kind or "")
        controller = str(self.controller or "")
        object_id = str(self.object_id or "") or None
        logical_id = str(self.logical_object_id or "") or None
        owner = str(self.owner or "") or None
        if not ref or not controller or kind not in {
            "any",
            "player",
            "permanent",
        }:
            raise DamageModifierError(
                "A damage subject requires a player or permanent identity"
            )
        if kind in {"any", "player"}:
            if any(value is not None for value in (object_id, logical_id, owner)):
                raise DamageModifierError(
                    "A nonpermanent damage subject cannot carry object identity"
                )
        elif not all((object_id, logical_id, owner)):
            raise DamageModifierError(
                "A permanent damage subject requires complete object identity"
            )
        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "controller", controller)
        object.__setattr__(self, "object_id", object_id)
        object.__setattr__(self, "logical_object_id", logical_id)
        object.__setattr__(self, "owner", owner)

    def event_conditions(self) -> dict[str, Any]:
        result: dict[str, Any] = {"amount": {"gt": 0}}
        if self.kind == "any":
            return result
        result["target"] = {"eq": self.ref}
        result["target_kind"] = {"eq": self.kind}
        if self.kind == "permanent":
            result["target_object_id"] = {"eq": self.object_id}
            result["target_logical_object_id"] = {
                "eq": self.logical_object_id
            }
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "controller": self.controller,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "owner": self.owner,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DamageSubject":
        _exact_fields(
            value,
            {
                "ref",
                "kind",
                "controller",
                "object_id",
                "logical_object_id",
                "owner",
            },
            label="Damage subject",
        )
        return cls(
            ref=str(value["ref"] or ""),
            kind=str(value["kind"] or ""),
            controller=str(value["controller"] or ""),
            object_id=(
                str(value["object_id"])
                if value["object_id"] is not None
                else None
            ),
            logical_object_id=(
                str(value["logical_object_id"])
                if value["logical_object_id"] is not None
                else None
            ),
            owner=(str(value["owner"]) if value["owner"] is not None else None),
        )

_CHOSEN_SOURCE_IDENTITY_FIELDS = (
    "logical_object_id",
    "oracle_id",
    "printed_name",
    "controller",
    "owner",
    "zone",
)
_PERMANENT_SPELL_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "planeswalker"}
)


def _normalize_chosen_source(source: ChosenDamageSource) -> None:
    for field_name in ("colors",):
        object.__setattr__(
            source,
            field_name,
            tuple(
                sorted(
                    {
                        str(value).upper()
                        for value in getattr(source, field_name)
                        if str(value)
                    }
                )
            ),
        )
    for field_name in (
        "types",
        "subtypes",
        "supertypes",
        "keywords",
    ):
        object.__setattr__(
            source,
            field_name,
            tuple(
                sorted(
                    {
                        str(value).casefold()
                        for value in getattr(source, field_name)
                        if str(value)
                    }
                )
            ),
        )
    for field_name in _CHOSEN_SOURCE_IDENTITY_FIELDS:
        raw = getattr(source, field_name)
        value = str(raw) if raw is not None else None
        if value == "":
            raise DamageModifierError(
                "Chosen source snapshot strings cannot be empty"
            )
        object.__setattr__(source, field_name, value)
    object.__setattr__(
        source,
        "identity_keys",
        tuple(
            sorted(
                {str(value) for value in source.identity_keys if str(value)}
            )
        ),
    )


def _expected_chosen_source_identity_keys(
    source: ChosenDamageSource,
) -> set[str]:
    expected = {f"{source.logical_object_id}|{source.zone}"}
    if source.zone == "stack" and set(source.types).intersection(
        _PERMANENT_SPELL_TYPES
    ):
        expected.add(f"{source.logical_object_id}|battlefield")
    return expected


def _validate_chosen_source(source: ChosenDamageSource) -> None:
    version = source.snapshot_version
    if type(version) is not int or version not in {0, 1, 2, 3}:
        raise DamageModifierError(
            "A chosen damage source has an unsupported snapshot version"
        )
    snapshot_values = (
        source.required_subtypes,
        source.required_supertypes,
        source.required_keywords,
        source.allowed_colors,
        source.types,
        source.subtypes,
        source.supertypes,
        source.colors,
        source.keywords,
    )
    if version == 0 and (
        any(
            getattr(source, field_name) is not None
            for field_name in _CHOSEN_SOURCE_IDENTITY_FIELDS
        )
        or any(snapshot_values)
    ):
        raise DamageModifierError(
            "Legacy chosen sources cannot carry versioned snapshot facts"
        )
    if version and not all(
        getattr(source, field_name)
        for field_name in _CHOSEN_SOURCE_IDENTITY_FIELDS
    ):
        raise DamageModifierError(
            "A versioned chosen source requires a complete identity snapshot"
        )
    if version in {2, 3} and not source.identity_keys:
        raise DamageModifierError(
            "An incarnation-safe chosen source requires identity keys"
        )
    if version in {2, 3} and set(source.identity_keys) != (
        _expected_chosen_source_identity_keys(source)
    ):
        raise DamageModifierError(
            "Chosen source identity keys do not match its snapshot"
        )
    if version not in {2, 3} and source.identity_keys:
        raise DamageModifierError(
            "Only incarnation-safe chosen sources carry identity keys"
        )
    if version not in {2, 3} and any(
        (
            source.predicate.subtypes_all,
            source.predicate.supertypes_all,
            source.predicate.keywords_all,
            source.predicate.colors_any,
        )
    ):
        raise DamageModifierError(
            "Only incarnation-safe chosen sources carry extended filters"
        )
    if version == 3:
        try:
            validate_chosen_damage_source_predicate(source.predicate)
        except ObjectQueryError as exc:
            raise DamageModifierError(str(exc)) from exc
    elif source.predicate.excluded_types or any(
        value is not None
        for value in (source.predicate.token, source.predicate.tapped)
    ):
        raise DamageModifierError(
            "Chosen source predicate uses unsupported changing characteristics"
        )


@dataclass(frozen=True, slots=True, init=False)
class ChosenDamageSource:
    ref: str
    object_id: str
    predicate: ObjectQuerySpec
    # Version zero is the additive Game Record v3 compatibility shape used by
    # historical checkpoints. Version one is the first complete LKI snapshot;
    # version two adds exact incarnation/permanent-spell continuity keys.
    # Version three stores one canonical ObjectQuerySpec instead of parallel
    # characteristic-filter fields.
    snapshot_version: int = 0
    logical_object_id: str | None = None
    oracle_id: str | None = None
    printed_name: str | None = None
    controller: str | None = None
    owner: str | None = None
    zone: str | None = None
    types: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()
    supertypes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    identity_keys: tuple[str, ...] = ()

    def __init__(
        self,
        ref: str,
        object_id: str,
        required_colors: tuple[str, ...] = (),
        allowed_colors: tuple[str, ...] = (),
        required_types: tuple[str, ...] = (),
        required_subtypes: tuple[str, ...] = (),
        required_supertypes: tuple[str, ...] = (),
        required_keywords: tuple[str, ...] = (),
        snapshot_version: int = 0,
        logical_object_id: str | None = None,
        oracle_id: str | None = None,
        printed_name: str | None = None,
        controller: str | None = None,
        owner: str | None = None,
        zone: str | None = None,
        types: tuple[str, ...] = (),
        subtypes: tuple[str, ...] = (),
        supertypes: tuple[str, ...] = (),
        colors: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        identity_keys: tuple[str, ...] = (),
        *,
        predicate: ObjectQuerySpec | None = None,
    ) -> None:
        legacy_filters = (
            required_colors,
            allowed_colors,
            required_types,
            required_subtypes,
            required_supertypes,
            required_keywords,
        )
        if predicate is not None and any(legacy_filters):
            raise DamageModifierError(
                "Chosen sources cannot mix canonical and legacy predicates"
            )
        try:
            canonical = predicate or ObjectQuerySpec(
                colors_all=required_colors,
                colors_any=allowed_colors,
                types_all=required_types,
                subtypes_all=required_subtypes,
                supertypes_all=required_supertypes,
                keywords_all=required_keywords,
            )
        except ObjectQueryError as exc:
            raise DamageModifierError(str(exc)) from exc
        for field_name, value in (
            ("ref", ref),
            ("object_id", object_id),
            ("predicate", canonical),
            ("snapshot_version", snapshot_version),
            ("logical_object_id", logical_object_id),
            ("oracle_id", oracle_id),
            ("printed_name", printed_name),
            ("controller", controller),
            ("owner", owner),
            ("zone", zone),
            ("types", tuple(types)),
            ("subtypes", tuple(subtypes)),
            ("supertypes", tuple(supertypes)),
            ("colors", tuple(colors)),
            ("keywords", tuple(keywords)),
            ("identity_keys", tuple(identity_keys)),
        ):
            object.__setattr__(self, field_name, value)
        self.__post_init__()

    def __post_init__(self) -> None:
        ref = str(self.ref or "")
        object_id = str(self.object_id or "")
        if not ref or not object_id:
            raise DamageModifierError(
                "A chosen damage source requires stable physical identity"
            )
        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "object_id", object_id)
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise DamageModifierError(
                "A chosen damage source requires a typed predicate"
            )
        _normalize_chosen_source(self)
        _validate_chosen_source(self)

    @property
    def required_colors(self) -> tuple[str, ...]:
        return self.predicate.colors_all

    @property
    def allowed_colors(self) -> tuple[str, ...]:
        return self.predicate.colors_any

    @property
    def required_types(self) -> tuple[str, ...]:
        return self.predicate.types_all

    @property
    def required_subtypes(self) -> tuple[str, ...]:
        return self.predicate.subtypes_all

    @property
    def required_supertypes(self) -> tuple[str, ...]:
        return self.predicate.supertypes_all

    @property
    def required_keywords(self) -> tuple[str, ...]:
        return self.predicate.keywords_all

    def event_conditions(self) -> dict[str, Any]:
        result: dict[str, Any] = (
            {"source_identity_key": {"in": list(self.identity_keys)}}
            if self.snapshot_version in {2, 3}
            else {"source_object_id": {"eq": self.object_id}}
        )
        color_conditions: dict[str, Any] = {}
        if self.required_colors:
            color_conditions["contains_all"] = list(self.required_colors)
        if self.allowed_colors:
            color_conditions["contains_any"] = list(self.allowed_colors)
        if color_conditions:
            result["source_colors"] = color_conditions
        if self.required_types:
            result["source_types"] = {
                "contains_all": list(self.required_types)
            }
        if self.required_subtypes:
            result["source_subtypes"] = {
                "contains_all": list(self.required_subtypes)
            }
        if self.required_supertypes:
            result["source_supertypes"] = {
                "contains_all": list(self.required_supertypes)
            }
        if self.required_keywords:
            result["source_keywords"] = {
                "contains_all": list(self.required_keywords)
            }
        if self.predicate.controller is not None:
            result["source_controller"] = {
                "eq": self.predicate.controller
            }
        if self.predicate.owner is not None:
            result["source_owner"] = {"eq": self.predicate.owner}
        return result

    def to_dict(self) -> dict[str, Any]:
        if self.snapshot_version == 3:
            return {
                "ref": self.ref,
                "object_id": self.object_id,
                "predicate": self.predicate.to_dict(),
                "snapshot_version": self.snapshot_version,
                "logical_object_id": self.logical_object_id,
                "oracle_id": self.oracle_id,
                "printed_name": self.printed_name,
                "controller": self.controller,
                "owner": self.owner,
                "zone": self.zone,
                "types": list(self.types),
                "subtypes": list(self.subtypes),
                "supertypes": list(self.supertypes),
                "colors": list(self.colors),
                "keywords": list(self.keywords),
                "identity_keys": list(self.identity_keys),
            }
        result = {
            "ref": self.ref,
            "object_id": self.object_id,
            "required_colors": list(self.required_colors),
            "required_types": list(self.required_types),
        }
        if self.snapshot_version == 2:
            result.update(
                {
                    "required_subtypes": list(self.required_subtypes),
                    "required_supertypes": list(self.required_supertypes),
                    "required_keywords": list(self.required_keywords),
                    "allowed_colors": list(self.allowed_colors),
                }
            )
        if self.snapshot_version:
            result.update(
                {
                    "snapshot_version": self.snapshot_version,
                    "logical_object_id": self.logical_object_id,
                    "oracle_id": self.oracle_id,
                    "printed_name": self.printed_name,
                    "controller": self.controller,
                    "owner": self.owner,
                    "zone": self.zone,
                    "types": list(self.types),
                    "subtypes": list(self.subtypes),
                    "supertypes": list(self.supertypes),
                    "colors": list(self.colors),
                    "keywords": list(self.keywords),
                    **(
                        {"identity_keys": list(self.identity_keys)}
                        if self.snapshot_version == 2
                        else {}
                    ),
                }
            )
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChosenDamageSource":
        legacy = {"ref", "object_id", "required_colors", "required_types"}
        versioned = legacy | {
            "snapshot_version",
            "logical_object_id",
            "oracle_id",
            "printed_name",
            "controller",
            "owner",
            "zone",
            "types",
            "subtypes",
            "supertypes",
            "colors",
            "keywords",
        }
        incarnation_safe = versioned | {
            "required_subtypes",
            "required_supertypes",
            "required_keywords",
            "allowed_colors",
            "identity_keys",
        }
        canonical = {
            "ref",
            "object_id",
            "predicate",
            "snapshot_version",
            "logical_object_id",
            "oracle_id",
            "printed_name",
            "controller",
            "owner",
            "zone",
            "types",
            "subtypes",
            "supertypes",
            "colors",
            "keywords",
            "identity_keys",
        }
        raw_snapshot_version = value.get("snapshot_version", 0)
        if type(raw_snapshot_version) is not int:
            raise DamageModifierError(
                "Chosen source snapshot versions must be integers"
            )
        snapshot_version = raw_snapshot_version
        _exact_fields(
            value,
            (
                canonical
                if snapshot_version == 3
                else incarnation_safe
                if snapshot_version == 2
                else (versioned if "snapshot_version" in value else legacy)
            ),
            label="Chosen damage source",
        )
        if snapshot_version == 3:
            raw_predicate = value["predicate"]
            try:
                predicate = ObjectQuerySpec.from_dict(raw_predicate)
            except ObjectQueryError as exc:
                raise DamageModifierError(str(exc)) from exc
            return cls(
                ref=str(value["ref"] or ""),
                object_id=str(value["object_id"] or ""),
                predicate=predicate,
                snapshot_version=snapshot_version,
                logical_object_id=value.get("logical_object_id"),
                oracle_id=value.get("oracle_id"),
                printed_name=value.get("printed_name"),
                controller=value.get("controller"),
                owner=value.get("owner"),
                zone=value.get("zone"),
                types=_strings(value.get("types", ()), label="Source snapshot types"),
                subtypes=_strings(value.get("subtypes", ()), label="Source snapshot subtypes"),
                supertypes=_strings(
                    value.get("supertypes", ()),
                    label="Source snapshot supertypes",
                ),
                colors=_strings(value.get("colors", ()), label="Source snapshot colors"),
                keywords=_strings(value.get("keywords", ()), label="Source snapshot keywords"),
                identity_keys=_strings(
                    value.get("identity_keys", ()),
                    label="Source identity keys",
                ),
            )
        return cls(
            ref=str(value["ref"] or ""),
            object_id=str(value["object_id"] or ""),
            required_colors=_strings(
                value["required_colors"], label="Required source colors"
            ),
            allowed_colors=_strings(
                value.get("allowed_colors", ()), label="Allowed source colors"
            ),
            required_types=_strings(
                value["required_types"], label="Required source types"
            ),
            required_subtypes=_strings(
                value.get("required_subtypes", ()),
                label="Required source subtypes",
            ),
            required_supertypes=_strings(
                value.get("required_supertypes", ()),
                label="Required source supertypes",
            ),
            required_keywords=_strings(
                value.get("required_keywords", ()),
                label="Required source keywords",
            ),
            snapshot_version=snapshot_version,
            logical_object_id=value.get("logical_object_id"),
            oracle_id=value.get("oracle_id"),
            printed_name=value.get("printed_name"),
            controller=value.get("controller"),
            owner=value.get("owner"),
            zone=value.get("zone"),
            types=_strings(value.get("types", ()), label="Source snapshot types"),
            subtypes=_strings(
                value.get("subtypes", ()), label="Source snapshot subtypes"
            ),
            supertypes=_strings(
                value.get("supertypes", ()), label="Source snapshot supertypes"
            ),
            colors=_strings(value.get("colors", ()), label="Source snapshot colors"),
            keywords=_strings(
                value.get("keywords", ()), label="Source snapshot keywords"
            ),
            identity_keys=_strings(
                value.get("identity_keys", ()), label="Source identity keys"
            ),
        )


@dataclass(frozen=True, slots=True)
class GainLifePreventionAftermath:
    player: str
    per_prevented: int = 0
    fixed_amount: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not str(self.player or ""):
            raise DamageModifierError("Prevention life gain requires a player")
        if self.schema_version != 1:
            raise DamageModifierError(
                "Unsupported prevention aftermath schema version"
            )
        if (
            type(self.per_prevented) is not int
            or self.per_prevented < 0
            or type(self.fixed_amount) is not int
            or self.fixed_amount < 0
            or not (self.per_prevented or self.fixed_amount)
        ):
            raise DamageModifierError(
                "Prevention life gain requires a positive fixed or scaled amount"
            )

    def amount(self, prevented: int) -> int:
        return self.fixed_amount + self.per_prevented * prevented

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "gain_life",
            "schema_version": self.schema_version,
            "player": self.player,
            "per_prevented": self.per_prevented,
            "fixed_amount": self.fixed_amount,
        }


@dataclass(frozen=True, slots=True)
class PlaceCountersPreventionAftermath:
    subject: DamageSubject
    counter_name: str
    placing_player: str
    per_prevented: int = 0
    fixed_amount: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subject, DamageSubject)
            or self.subject.kind != "permanent"
        ):
            raise DamageModifierError(
                "Prevention counter aftermath requires a permanent"
            )
        counter = " ".join(str(self.counter_name).casefold().split())
        if not counter or not str(self.placing_player or ""):
            raise DamageModifierError(
                "Prevention counter aftermath requires counter and player"
            )
        object.__setattr__(self, "counter_name", counter)
        if self.schema_version != 1:
            raise DamageModifierError(
                "Unsupported prevention aftermath schema version"
            )
        if (
            type(self.per_prevented) is not int
            or self.per_prevented < 0
            or type(self.fixed_amount) is not int
            or self.fixed_amount < 0
            or not (self.per_prevented or self.fixed_amount)
        ):
            raise DamageModifierError(
                "Prevention counter aftermath requires a positive fixed or scaled amount"
            )

    def amount(self, prevented: int) -> int:
        return self.fixed_amount + self.per_prevented * prevented

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "place_counters",
            "schema_version": self.schema_version,
            "subject": self.subject.to_dict(),
            "counter_name": self.counter_name,
            "placing_player": self.placing_player,
            "per_prevented": self.per_prevented,
            "fixed_amount": self.fixed_amount,
        }


@dataclass(frozen=True, slots=True)
class DamageAftermathRecipient:
    """Closed recipient vocabulary for a CR 615.5 damage result."""

    kind: str
    subject: DamageSubject | None = None

    def __post_init__(self) -> None:
        if self.kind == "fixed":
            if not isinstance(self.subject, DamageSubject) or self.subject.kind == "any":
                raise DamageModifierError(
                    "A fixed prevention-damage recipient requires a player or permanent"
                )
            return
        if self.kind == "prevented_source_controller" and self.subject is None:
            return
        raise DamageModifierError(
            "Prevention-damage recipient kind is unsupported or malformed"
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.subject is not None:
            result["subject"] = self.subject.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DamageAftermathRecipient":
        if not isinstance(value, Mapping):
            raise DamageModifierError(
                "Prevention-damage recipient must be an object"
            )
        kind = value.get("kind")
        expected = {"kind", "subject"} if kind == "fixed" else {"kind"}
        _exact_fields(value, expected, label="Prevention-damage recipient")
        raw_subject = value.get("subject")
        if raw_subject is not None and not isinstance(raw_subject, Mapping):
            raise DamageModifierError(
                "Prevention-damage recipient subject must be an object"
            )
        return cls(
            kind=str(kind or ""),
            subject=(
                DamageSubject.from_dict(raw_subject)
                if isinstance(raw_subject, Mapping)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class DealDamagePreventionAftermath:
    """Typed CR 615.5 damage instruction pinned at shield creation."""

    source: DamageSourceSnapshot
    recipient: DamageAftermathRecipient
    per_prevented: int = 0
    fixed_amount: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.source, DamageSourceSnapshot) or not isinstance(
            self.recipient, DamageAftermathRecipient
        ):
            raise DamageModifierError(
                "Prevention damage aftermath requires typed source and recipient"
            )
        if self.schema_version != 1:
            raise DamageModifierError(
                "Unsupported prevention aftermath schema version"
            )
        if (
            type(self.per_prevented) is not int
            or self.per_prevented < 0
            or type(self.fixed_amount) is not int
            or self.fixed_amount < 0
            or not (self.per_prevented or self.fixed_amount)
        ):
            raise DamageModifierError(
                "Prevention damage requires a positive fixed or scaled amount"
            )

    def amount(self, prevented: int) -> int:
        return self.fixed_amount + self.per_prevented * prevented

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "deal_damage",
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "recipient": self.recipient.to_dict(),
            "per_prevented": self.per_prevented,
            "fixed_amount": self.fixed_amount,
        }


PreventionAftermath: TypeAlias = (
    GainLifePreventionAftermath
    | PlaceCountersPreventionAftermath
    | DealDamagePreventionAftermath
)


def prevention_aftermath_from_dict(
    value: Mapping[str, Any],
) -> PreventionAftermath:
    if not isinstance(value, Mapping):
        raise DamageModifierError("Prevention aftermath must be an object")
    kind = value.get("kind")
    if kind == "gain_life":
        _exact_fields(
            value,
            {
                "kind",
                "schema_version",
                "player",
                "per_prevented",
                "fixed_amount",
            },
            label="Prevention life aftermath",
        )
        return GainLifePreventionAftermath(
            player=str(value["player"] or ""),
            per_prevented=value["per_prevented"],
            fixed_amount=value["fixed_amount"],
            schema_version=value["schema_version"],
        )
    if kind == "place_counters":
        _exact_fields(
            value,
            {
                "kind",
                "schema_version",
                "subject",
                "counter_name",
                "placing_player",
                "per_prevented",
                "fixed_amount",
            },
            label="Prevention counter aftermath",
        )
        subject = value["subject"]
        if not isinstance(subject, Mapping):
            raise DamageModifierError(
                "Prevention counter aftermath subject must be an object"
            )
        return PlaceCountersPreventionAftermath(
            subject=DamageSubject.from_dict(subject),
            counter_name=str(value["counter_name"] or ""),
            placing_player=str(value["placing_player"] or ""),
            per_prevented=value["per_prevented"],
            fixed_amount=value["fixed_amount"],
            schema_version=value["schema_version"],
        )
    if kind == "deal_damage":
        _exact_fields(
            value,
            {
                "kind",
                "schema_version",
                "source",
                "recipient",
                "per_prevented",
                "fixed_amount",
            },
            label="Prevention damage aftermath",
        )
        source = value["source"]
        recipient = value["recipient"]
        if not isinstance(source, Mapping) or not isinstance(recipient, Mapping):
            raise DamageModifierError(
                "Prevention damage aftermath source or recipient is malformed"
            )
        try:
            source_snapshot = DamageSourceSnapshot.from_dict(dict(source))
        except DamageError as exc:
            raise DamageModifierError(str(exc)) from exc
        return DealDamagePreventionAftermath(
            source=source_snapshot,
            recipient=DamageAftermathRecipient.from_dict(recipient),
            per_prevented=value["per_prevented"],
            fixed_amount=value["fixed_amount"],
            schema_version=value["schema_version"],
        )
    raise DamageModifierError("Unknown prevention aftermath kind")


@dataclass(frozen=True, slots=True)
class DamagePreventionScope:
    """Public applicability shared by static and durable prevention."""

    source_controller_relation: str = "any"
    target_controller_relation: str = "any"
    target_kinds: tuple[str, ...] = ()
    source_characteristics_all: tuple[str, ...] = ()
    source_characteristics_any: tuple[str, ...] = ()
    source_characteristics_none: tuple[str, ...] = ()
    source_colors_any: tuple[str, ...] = ()
    source_colors_none: tuple[str, ...] = ()
    target_characteristics_all: tuple[str, ...] = ()
    target_characteristics_any: tuple[str, ...] = ()
    target_characteristics_none: tuple[str, ...] = ()
    source_ref: str | None = None
    target_ref: str | None = None
    excluded_source_ref: str | None = None
    excluded_target_ref: str | None = None

    def __post_init__(self) -> None:
        relations = {"any", "source_controller", "opponent"}
        if (
            self.source_controller_relation not in relations
            or self.target_controller_relation not in relations
        ):
            raise DamageModifierError(
                "Prevention scope controller relation is unsupported"
            )
        for field in (
            "target_kinds",
            "source_characteristics_all",
            "source_characteristics_any",
            "source_characteristics_none",
            "source_colors_any",
            "source_colors_none",
            "target_characteristics_all",
            "target_characteristics_any",
            "target_characteristics_none",
        ):
            values = tuple(getattr(self, field))
            if any(type(value) is not str or not value for value in values):
                raise DamageModifierError(
                    f"Prevention scope {field} must contain strings"
                )
            normalized = tuple(sorted(set(values)))
            if values != normalized:
                raise DamageModifierError(
                    f"Prevention scope {field} must be canonical"
                )
        if set(self.target_kinds) - {"player", "permanent"}:
            raise DamageModifierError(
                "Prevention scope target kind is unsupported"
            )
        if set(self.source_colors_any).union(self.source_colors_none) - set(
            "WUBRG"
        ):
            raise DamageModifierError(
                "Prevention scope source color is unsupported"
            )
        for field in (
            "source_ref",
            "target_ref",
            "excluded_source_ref",
            "excluded_target_ref",
        ):
            value = getattr(self, field)
            if value is not None and (type(value) is not str or not value):
                raise DamageModifierError(
                    f"Prevention scope {field} must be a nonempty reference"
                )
        if self.source_ref is not None and self.excluded_source_ref is not None:
            raise DamageModifierError(
                "Prevention scope source identity predicates conflict"
            )
        if self.target_ref is not None and self.excluded_target_ref is not None:
            raise DamageModifierError(
                "Prevention scope target identity predicates conflict"
            )

    @staticmethod
    def _relation(
        relation: str,
        controller: str,
    ) -> Mapping[str, Any] | None:
        if relation == "any":
            return None
        if relation == "source_controller":
            return {"eq": controller}
        return {"not_in": [controller, None]}

    @staticmethod
    def _collection_conditions(
        *,
        all_values: tuple[str, ...],
        any_values: tuple[str, ...],
        none_values: tuple[str, ...],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if all_values:
            result["contains_all"] = list(all_values)
        if any_values:
            result["contains_any"] = list(any_values)
        if none_values:
            result["contains_none"] = list(none_values)
        return result

    def event_conditions(self, *, controller: str) -> dict[str, Any]:
        if type(controller) is not str or not controller:
            raise DamageModifierError(
                "Prevention scope requires an active controller"
            )
        result: dict[str, Any] = {}
        source_controller = self._relation(
            self.source_controller_relation, controller
        )
        if source_controller is not None:
            result["source_controller"] = source_controller
        target_controller = self._relation(
            self.target_controller_relation, controller
        )
        if target_controller is not None:
            result["target_controller"] = target_controller
        if self.target_kinds:
            result["target_kind"] = {"in": list(self.target_kinds)}
        source_characteristics = self._collection_conditions(
            all_values=self.source_characteristics_all,
            any_values=self.source_characteristics_any,
            none_values=self.source_characteristics_none,
        )
        if source_characteristics:
            result["source_characteristics"] = source_characteristics
        source_colors = self._collection_conditions(
            all_values=(),
            any_values=self.source_colors_any,
            none_values=self.source_colors_none,
        )
        if source_colors:
            result["source_colors"] = source_colors
        target_characteristics = self._collection_conditions(
            all_values=self.target_characteristics_all,
            any_values=self.target_characteristics_any,
            none_values=self.target_characteristics_none,
        )
        if target_characteristics:
            result["target_characteristics"] = target_characteristics
        if self.source_ref is not None:
            result["source"] = {"eq": self.source_ref}
        elif self.excluded_source_ref is not None:
            result["source"] = {"not_in": [self.excluded_source_ref]}
        if self.target_ref is not None:
            result["target"] = {"eq": self.target_ref}
        elif self.excluded_target_ref is not None:
            result["target"] = {"not_in": [self.excluded_target_ref]}
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_controller_relation": self.source_controller_relation,
            "target_controller_relation": self.target_controller_relation,
            "target_kinds": list(self.target_kinds),
            "source_characteristics_all": list(self.source_characteristics_all),
            "source_characteristics_any": list(self.source_characteristics_any),
            "source_characteristics_none": list(self.source_characteristics_none),
            "source_colors_any": list(self.source_colors_any),
            "source_colors_none": list(self.source_colors_none),
            "target_characteristics_all": list(self.target_characteristics_all),
            "target_characteristics_any": list(self.target_characteristics_any),
            "target_characteristics_none": list(self.target_characteristics_none),
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "excluded_source_ref": self.excluded_source_ref,
            "excluded_target_ref": self.excluded_target_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DamagePreventionScope":
        expected = set(cls().to_dict())
        _exact_fields(value, expected, label="Damage prevention scope")
        return cls(
            source_controller_relation=str(value["source_controller_relation"]),
            target_controller_relation=str(value["target_controller_relation"]),
            target_kinds=_strings(value["target_kinds"], label="scope target kinds"),
            source_characteristics_all=_strings(
                value["source_characteristics_all"],
                label="scope source all characteristics",
            ),
            source_characteristics_any=_strings(
                value["source_characteristics_any"],
                label="scope source any characteristics",
            ),
            source_characteristics_none=_strings(
                value["source_characteristics_none"],
                label="scope source excluded characteristics",
            ),
            source_colors_any=_strings(
                value["source_colors_any"], label="scope source any colors"
            ),
            source_colors_none=_strings(
                value["source_colors_none"], label="scope source excluded colors"
            ),
            target_characteristics_all=_strings(
                value["target_characteristics_all"],
                label="scope target all characteristics",
            ),
            target_characteristics_any=_strings(
                value["target_characteristics_any"],
                label="scope target any characteristics",
            ),
            target_characteristics_none=_strings(
                value["target_characteristics_none"],
                label="scope target excluded characteristics",
            ),
            source_ref=(
                str(value["source_ref"])
                if value["source_ref"] is not None
                else None
            ),
            target_ref=(
                str(value["target_ref"])
                if value["target_ref"] is not None
                else None
            ),
            excluded_source_ref=(
                str(value["excluded_source_ref"])
                if value["excluded_source_ref"] is not None
                else None
            ),
            excluded_target_ref=(
                str(value["excluded_target_ref"])
                if value["excluded_target_ref"] is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class DamagePreventionShield:
    shield_id: str
    source_id: str
    controller: str
    subject: DamageSubject
    mode: PreventionMode
    remaining: int | None
    duration: DamageModifierDuration
    created_turn_sequence: int
    damage_kind: PreventionDamageKind = PreventionDamageKind.ANY
    recipient_kind: PreventionRecipientKind = PreventionRecipientKind.ANY
    scope: DamagePreventionScope = DamagePreventionScope()
    chosen_source: ChosenDamageSource | None = None
    label: str = ""
    application_group_id: str | None = None
    aftermath: tuple[PreventionAftermath, ...] = ()
    triggered_ability: PreventionTriggeredAbility | None = None

    def __post_init__(self) -> None:
        if not all((self.shield_id, self.source_id, self.controller)):
            raise DamageModifierError(
                "A prevention shield requires stable identity and controller"
            )
        if not isinstance(self.subject, DamageSubject):
            raise DamageModifierError("A prevention shield requires a subject")
        if not isinstance(self.mode, PreventionMode) or not isinstance(
            self.duration, DamageModifierDuration
        ):
            raise DamageModifierError(
                "A prevention shield requires typed mode and duration"
            )
        if not isinstance(self.damage_kind, PreventionDamageKind) or not isinstance(
            self.recipient_kind, PreventionRecipientKind
        ):
            raise DamageModifierError(
                "A prevention shield requires typed damage and recipient scope"
            )
        if not isinstance(self.scope, DamagePreventionScope):
            raise DamageModifierError(
                "A prevention shield scope must be typed"
            )
        if self.mode == PreventionMode.AMOUNT:
            if type(self.remaining) is not int or self.remaining < 1:
                raise DamageModifierError(
                    "An amount shield requires a positive remaining amount"
                )
        elif self.remaining is not None:
            raise DamageModifierError(
                "Only an amount shield may carry a remaining amount"
            )
        if (
            type(self.created_turn_sequence) is not int
            or self.created_turn_sequence < 0
        ):
            raise DamageModifierError(
                "A prevention shield requires a nonnegative creation turn"
            )
        if self.chosen_source is not None and not isinstance(
            self.chosen_source, ChosenDamageSource
        ):
            raise DamageModifierError(
                "A prevention shield chosen source must be typed"
            )
        if self.application_group_id is not None and (
            type(self.application_group_id) is not str
            or not self.application_group_id
        ):
            raise DamageModifierError(
                "A prevention application-group identity must be nonempty or null"
            )
        aftermath = tuple(self.aftermath)
        if any(
            not isinstance(
                value,
                (
                    GainLifePreventionAftermath,
                    PlaceCountersPreventionAftermath,
                    DealDamagePreventionAftermath,
                ),
            )
            for value in aftermath
        ):
            raise DamageModifierError(
                "A prevention shield aftermath must use typed values"
            )
        object.__setattr__(self, "aftermath", aftermath)
        if self.triggered_ability is not None and not isinstance(
            self.triggered_ability, PreventionTriggeredAbility
        ):
            raise DamageModifierError(
                "A prevention shield triggered ability must be typed"
            )

    @property
    def effect_id(self) -> str:
        return f"prevention.shield:{self.shield_id}"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "shield_id": self.shield_id,
            "source_id": self.source_id,
            "controller": self.controller,
            "subject": self.subject.to_dict(),
            "mode": self.mode.value,
            "remaining": self.remaining,
            "duration": self.duration.value,
            "created_turn_sequence": self.created_turn_sequence,
            "chosen_source": (
                self.chosen_source.to_dict()
                if self.chosen_source is not None
                else None
            ),
            "label": self.label,
        }
        if self.damage_kind != PreventionDamageKind.ANY:
            result["damage_kind"] = self.damage_kind.value
        if self.recipient_kind != PreventionRecipientKind.ANY:
            result["recipient_kind"] = self.recipient_kind.value
        if self.scope != DamagePreventionScope():
            result["scope"] = self.scope.to_dict()
        if self.application_group_id is not None:
            result["application_group_id"] = self.application_group_id
        if self.aftermath:
            result["aftermath"] = [value.to_dict() for value in self.aftermath]
        if self.triggered_ability is not None:
            result["triggered_ability"] = self.triggered_ability.to_dict()
        return result

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "DamagePreventionShield":
        expected = {
            "shield_id",
            "source_id",
            "controller",
            "subject",
            "mode",
            "remaining",
            "duration",
            "created_turn_sequence",
            "chosen_source",
            "label",
        }
        optional = {
            field
            for field in (
                "aftermath",
                "application_group_id",
                "damage_kind",
                "recipient_kind",
                "triggered_ability",
                "scope",
            )
            if field in value
        }
        _exact_fields(value, expected | optional, label="Prevention shield")
        subject = value["subject"]
        chosen = value["chosen_source"]
        if not isinstance(subject, Mapping) or (
            chosen is not None and not isinstance(chosen, Mapping)
        ):
            raise DamageModifierError(
                "Prevention shield nested values are malformed"
            )
        try:
            mode = PreventionMode(str(value["mode"]))
            duration = DamageModifierDuration(str(value["duration"]))
            damage_kind = PreventionDamageKind(
                str(value.get("damage_kind") or "any")
            )
            recipient_kind = PreventionRecipientKind(
                str(value.get("recipient_kind") or "any")
            )
        except ValueError as exc:
            raise DamageModifierError(
                "Prevention shield mode, duration, or scope is unsupported"
            ) from exc
        raw_trigger = value.get("triggered_ability")
        if raw_trigger is not None and not isinstance(raw_trigger, Mapping):
            raise DamageModifierError(
                "Prevention shield triggered ability is malformed"
            )
        raw_scope = value.get("scope")
        if raw_scope is not None and not isinstance(raw_scope, Mapping):
            raise DamageModifierError(
                "Prevention shield applicability scope is malformed"
            )
        try:
            triggered_ability = (
                PreventionTriggeredAbility.from_dict(raw_trigger)
                if isinstance(raw_trigger, Mapping)
                else None
            )
        except PreventionTriggerError as exc:
            raise DamageModifierError(str(exc)) from exc
        return cls(
            shield_id=str(value["shield_id"] or ""),
            source_id=str(value["source_id"] or ""),
            controller=str(value["controller"] or ""),
            subject=DamageSubject.from_dict(subject),
            mode=mode,
            remaining=value["remaining"],
            duration=duration,
            created_turn_sequence=value["created_turn_sequence"],
            damage_kind=damage_kind,
            recipient_kind=recipient_kind,
            scope=(
                DamagePreventionScope.from_dict(raw_scope)
                if isinstance(raw_scope, Mapping)
                else DamagePreventionScope()
            ),
            chosen_source=(
                ChosenDamageSource.from_dict(chosen)
                if isinstance(chosen, Mapping)
                else None
            ),
            label=str(value["label"] or ""),
            application_group_id=(
                str(value["application_group_id"])
                if value.get("application_group_id") is not None
                else None
            ),
            aftermath=tuple(
                prevention_aftermath_from_dict(item)
                for item in value.get("aftermath", ())
            ),
            triggered_ability=triggered_ability,
        )


@dataclass(frozen=True, slots=True)
class DamageRedirectionEffect:
    redirection_id: str
    source_id: str
    controller: str
    subject: DamageSubject
    destination: DamageSubject
    duration: DamageModifierDuration
    created_turn_sequence: int
    chosen_source: ChosenDamageSource | None = None
    consume_on_application: bool = True
    label: str = ""

    def __post_init__(self) -> None:
        if not all((self.redirection_id, self.source_id, self.controller)):
            raise DamageModifierError(
                "A redirection effect requires stable identity and controller"
            )
        if not isinstance(self.subject, DamageSubject) or not isinstance(
            self.destination, DamageSubject
        ):
            raise DamageModifierError(
                "A redirection effect requires typed subjects"
            )
        if self.destination.kind == "any":
            raise DamageModifierError(
                "A damage redirection requires a concrete destination"
            )
        if not isinstance(self.duration, DamageModifierDuration):
            raise DamageModifierError(
                "A redirection effect requires a typed duration"
            )
        if type(self.consume_on_application) is not bool:
            raise DamageModifierError(
                "Redirection consumption policy must be a boolean"
            )
        if (
            type(self.created_turn_sequence) is not int
            or self.created_turn_sequence < 0
        ):
            raise DamageModifierError(
                "A redirection effect requires a nonnegative creation turn"
            )

    @property
    def effect_id(self) -> str:
        return f"damage.redirection:{self.redirection_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "redirection_id": self.redirection_id,
            "source_id": self.source_id,
            "controller": self.controller,
            "subject": self.subject.to_dict(),
            "destination": self.destination.to_dict(),
            "duration": self.duration.value,
            "created_turn_sequence": self.created_turn_sequence,
            "chosen_source": (
                self.chosen_source.to_dict()
                if self.chosen_source is not None
                else None
            ),
            "consume_on_application": self.consume_on_application,
            "label": self.label,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "DamageRedirectionEffect":
        _exact_fields(
            value,
            {
                "redirection_id",
                "source_id",
                "controller",
                "subject",
                "destination",
                "duration",
                "created_turn_sequence",
                "chosen_source",
                "consume_on_application",
                "label",
            },
            label="Damage redirection",
        )
        subject = value["subject"]
        destination = value["destination"]
        chosen = value["chosen_source"]
        if (
            not isinstance(subject, Mapping)
            or not isinstance(destination, Mapping)
            or (chosen is not None and not isinstance(chosen, Mapping))
        ):
            raise DamageModifierError(
                "Damage redirection nested values are malformed"
            )
        try:
            duration = DamageModifierDuration(str(value["duration"]))
        except ValueError as exc:
            raise DamageModifierError(
                "Damage redirection duration is unsupported"
            ) from exc
        return cls(
            redirection_id=str(value["redirection_id"] or ""),
            source_id=str(value["source_id"] or ""),
            controller=str(value["controller"] or ""),
            subject=DamageSubject.from_dict(subject),
            destination=DamageSubject.from_dict(destination),
            duration=duration,
            created_turn_sequence=value["created_turn_sequence"],
            chosen_source=(
                ChosenDamageSource.from_dict(chosen)
                if isinstance(chosen, Mapping)
                else None
            ),
            consume_on_application=value["consume_on_application"],
            label=str(value["label"] or ""),
        )
