from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .counter_names import CounterStateError, normalized_counter_name


class ObjectQueryError(ValueError):
    """A generic object predicate is malformed or noncanonical."""


_QUERY_FIELDS = frozenset({
    "zones",
    "owner",
    "controller",
    "types_all",
    "types_any",
    "excluded_types",
    "subtypes_all",
    "supertypes_all",
    "colors_all",
    "colors_any",
    "keywords_all",
    "token",
    "tapped",
    "include_phased_out",
    "known_to_actor",
    "exclude_ref",
})
_LEGACY_QUERY_FIELDS = _QUERY_FIELDS - {"types_any"}
_EXTENDED_QUERY_FIELDS = _QUERY_FIELDS | {
    "subtypes_any",
    "excluded_subtypes",
    "colorless",
    "state_predicate",
}
_RELATIONAL_QUERY_FIELDS = _EXTENDED_QUERY_FIELDS | {
    "excluded_controllers",
    "minimum_color_count",
}
_LEGACY_PERMANENT_STATE_FIELDS = frozenset(
    {
        "entered_this_turn",
        "tapped",
        "counter_name",
        "minimum_counter_count",
    }
)
_PERMANENT_STATE_FIELDS = _LEGACY_PERMANENT_STATE_FIELDS | {
    "attacking",
    "blocking",
    "enchanted",
    "equipped",
    "modified",
    "monstrous",
}


@dataclass(frozen=True, slots=True)
class PermanentStatePredicateSpec:
    """One closed public-state predicate shared by targets and affected sets."""

    entered_this_turn: bool = False
    tapped: bool | None = None
    attacking: bool | None = None
    blocking: bool | None = None
    enchanted: bool | None = None
    equipped: bool | None = None
    modified: bool | None = None
    monstrous: bool | None = None
    counter_name: str | None = None
    minimum_counter_count: int | None = None
    _serialization_version: int = field(
        default=1,
        repr=False,
        compare=False,
        hash=False,
    )

    def __post_init__(self) -> None:
        if type(self.entered_this_turn) is not bool:
            raise ObjectQueryError(
                "Permanent-state entered-this-turn must be a boolean"
            )
        for field_name in (
            "tapped",
            "attacking",
            "blocking",
            "enchanted",
            "equipped",
            "modified",
            "monstrous",
        ):
            value = getattr(self, field_name)
            if value is not None and type(value) is not bool:
                raise ObjectQueryError(
                    f"Permanent-state {field_name} value must be boolean or null"
                )
        counter_name = self.counter_name
        minimum = self.minimum_counter_count
        if counter_name is None:
            if minimum is not None:
                raise ObjectQueryError(
                    "Permanent-state counter minimum requires a counter name"
                )
        else:
            if type(counter_name) is not str:
                raise ObjectQueryError(
                    "Permanent-state counter name must be a string"
                )
            try:
                counter_name = normalized_counter_name(counter_name)
            except CounterStateError as exc:
                raise ObjectQueryError(str(exc)) from exc
            if type(minimum) is not int or minimum <= 0:
                raise ObjectQueryError(
                    "Permanent-state counter minimum must be positive"
                )
            object.__setattr__(self, "counter_name", counter_name)
        if (
            not self.entered_this_turn
            and self.tapped is None
            and self.attacking is None
            and self.blocking is None
            and self.enchanted is None
            and self.equipped is None
            and self.modified is None
            and self.monstrous is None
            and counter_name is None
        ):
            raise ObjectQueryError(
                "Permanent-state predicate must constrain public state"
            )
        extended = any(
            getattr(self, field_name) is not None
            for field_name in (
                "attacking",
                "blocking",
                "enchanted",
                "equipped",
                "modified",
                "monstrous",
            )
        )
        if extended and self._serialization_version == 1:
            object.__setattr__(self, "_serialization_version", 2)
        if (
            type(self._serialization_version) is not int
            or self._serialization_version not in {1, 2}
        ):
            raise ObjectQueryError(
                "Permanent-state predicate serialization version is unsupported"
            )
        if extended and self._serialization_version != 2:
            raise ObjectQueryError(
                "Extended permanent-state predicates require serialization version 2"
            )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "entered_this_turn": self.entered_this_turn,
            "tapped": self.tapped,
            "counter_name": self.counter_name,
            "minimum_counter_count": self.minimum_counter_count,
        }
        if self._serialization_version == 2:
            value.update(
                {
                    "attacking": self.attacking,
                    "blocking": self.blocking,
                    "enchanted": self.enchanted,
                    "equipped": self.equipped,
                    "modified": self.modified,
                    "monstrous": self.monstrous,
                }
            )
        return value

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "PermanentStatePredicateSpec":
        if not isinstance(value, Mapping) or frozenset(value) not in {
            _LEGACY_PERMANENT_STATE_FIELDS,
            _PERMANENT_STATE_FIELDS,
        }:
            raise ObjectQueryError(
                "Permanent-state predicate fields are incomplete or unknown"
            )
        extended = frozenset(value) == _PERMANENT_STATE_FIELDS
        return cls(
            entered_this_turn=value["entered_this_turn"],
            tapped=value["tapped"],
            attacking=value.get("attacking"),
            blocking=value.get("blocking"),
            enchanted=value.get("enchanted"),
            equipped=value.get("equipped"),
            modified=value.get("modified"),
            monstrous=value.get("monstrous"),
            counter_name=value["counter_name"],
            minimum_counter_count=value["minimum_counter_count"],
            _serialization_version=2 if extended else 1,
        )


def permanent_state_predicate_matches(
    spec: PermanentStatePredicateSpec,
    *,
    counters: Mapping[str, Any],
    entered_this_turn: bool,
    tapped: bool,
    attacking: bool = False,
    blocking: bool = False,
    enchanted: bool = False,
    equipped: bool = False,
    modified: bool = False,
    monstrous: bool = False,
) -> bool:
    """Evaluate one typed predicate over current public permanent state."""

    if not isinstance(spec, PermanentStatePredicateSpec):
        raise ObjectQueryError(
            "Permanent-state matching requires a typed predicate"
        )
    if not isinstance(counters, Mapping):
        raise ObjectQueryError("Permanent-state counters must be a mapping")
    if type(entered_this_turn) is not bool:
        raise ObjectQueryError(
            "Permanent-state turn history must be a boolean"
        )
    for field_name, value in (
        ("tapped", tapped),
        ("attacking", attacking),
        ("blocking", blocking),
        ("enchanted", enchanted),
        ("equipped", equipped),
        ("modified", modified),
        ("monstrous", monstrous),
    ):
        if type(value) is not bool:
            raise ObjectQueryError(
                f"Permanent-state {field_name} state must be a boolean"
            )
    if spec.entered_this_turn and not entered_this_turn:
        return False
    if spec.tapped is not None and tapped is not spec.tapped:
        return False
    for field_name, value in (
        ("attacking", attacking),
        ("blocking", blocking),
        ("enchanted", enchanted),
        ("equipped", equipped),
        ("modified", modified),
        ("monstrous", monstrous),
    ):
        expected = getattr(spec, field_name)
        if expected is not None and value is not expected:
            return False
    if spec.counter_name is not None:
        raw = counters.get(spec.counter_name, 0)
        if type(raw) is not int or raw < 0:
            raise ObjectQueryError(
                "Permanent-state counter amounts must be nonnegative integers"
            )
        assert spec.minimum_counter_count is not None
        if raw < spec.minimum_counter_count:
            return False
    return True


def _normalized_terms(
    values: Iterable[str], *, field_name: str, upper: bool = False
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ObjectQueryError(f"Object query {field_name} must be an array")
    normalize = str.upper if upper else str.casefold
    normalized: list[str] = []
    for value in values:
        if type(value) is not str or not value:
            raise ObjectQueryError(
                f"Object query {field_name} requires nonempty strings"
            )
        normalized.append(normalize(value))
    result = tuple(sorted(normalized))
    if len(set(result)) != len(result):
        raise ObjectQueryError(
            f"Object query {field_name} requires unique normalized strings"
        )
    return result


@dataclass(frozen=True, slots=True)
class ObjectQuerySpec:
    zones: tuple[str, ...] = ()
    owner: str | None = None
    controller: str | None = None
    excluded_controllers: tuple[str, ...] = ()
    types_all: tuple[str, ...] = ()
    types_any: tuple[str, ...] = ()
    excluded_types: tuple[str, ...] = ()
    subtypes_all: tuple[str, ...] = ()
    subtypes_any: tuple[str, ...] = ()
    excluded_subtypes: tuple[str, ...] = ()
    supertypes_all: tuple[str, ...] = ()
    colors_all: tuple[str, ...] = ()
    colors_any: tuple[str, ...] = ()
    colorless: bool | None = None
    minimum_color_count: int | None = None
    keywords_all: tuple[str, ...] = ()
    token: bool | None = None
    tapped: bool | None = None
    include_phased_out: bool = False
    known_to_actor: bool | None = None
    exclude_ref: str | None = None
    state_predicate: PermanentStatePredicateSpec | None = None
    _serialization_version: int = field(
        default=2,
        repr=False,
        compare=False,
        hash=False,
    )

    def __post_init__(self) -> None:
        for field_name in (
            "zones",
            "types_all",
            "types_any",
            "excluded_types",
            "subtypes_all",
            "subtypes_any",
            "excluded_subtypes",
            "supertypes_all",
            "keywords_all",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalized_terms(
                    getattr(self, field_name), field_name=field_name
                ),
            )
        for field_name in ("colors_all", "colors_any"):
            object.__setattr__(
                self,
                field_name,
                _normalized_terms(
                    getattr(self, field_name),
                    field_name=field_name,
                    upper=True,
                ),
            )
        for field_name in ("owner", "controller", "exclude_ref"):
            value = getattr(self, field_name)
            if value is not None:
                if type(value) is not str or not value:
                    raise ObjectQueryError(
                        f"Object query {field_name} must be a nonempty string or null"
                    )
        excluded_controllers: list[str] = []
        if not isinstance(self.excluded_controllers, (list, tuple)):
            raise ObjectQueryError(
                "Object query excluded_controllers must be an array"
            )
        for value in self.excluded_controllers:
            if type(value) is not str or not value:
                raise ObjectQueryError(
                    "Object query excluded_controllers requires nonempty strings"
                )
            excluded_controllers.append(value)
        excluded_controllers.sort()
        if len(set(excluded_controllers)) != len(excluded_controllers):
            raise ObjectQueryError(
                "Object query excluded_controllers requires unique strings"
            )
        object.__setattr__(
            self, "excluded_controllers", tuple(excluded_controllers)
        )
        for field_name in (
            "token",
            "tapped",
            "known_to_actor",
            "colorless",
        ):
            value = getattr(self, field_name)
            if value is not None and type(value) is not bool:
                raise ObjectQueryError(
                    f"Object query {field_name} must be boolean or null"
                )
        if type(self.include_phased_out) is not bool:
            raise ObjectQueryError(
                "Object query include_phased_out must be boolean"
            )
        if self.minimum_color_count is not None and (
            type(self.minimum_color_count) is not int
            or self.minimum_color_count <= 0
            or self.minimum_color_count > 5
        ):
            raise ObjectQueryError(
                "Object query minimum_color_count must be an integer from 1 through 5 or null"
            )
        if self.state_predicate is not None and not isinstance(
            self.state_predicate, PermanentStatePredicateSpec
        ):
            raise ObjectQueryError(
                "Object query state predicate must be typed or null"
            )
        relational = bool(
            self.excluded_controllers
            or self.minimum_color_count is not None
        )
        extended = bool(
            self.subtypes_any
            or self.excluded_subtypes
            or self.colorless is not None
            or self.state_predicate is not None
        )
        if relational and self._serialization_version in {1, 2, 3}:
            object.__setattr__(self, "_serialization_version", 4)
        elif extended and self._serialization_version == 2:
            object.__setattr__(self, "_serialization_version", 3)
        if self._serialization_version not in {1, 2, 3, 4}:
            raise ObjectQueryError(
                "Object query serialization version is unsupported"
            )
        if extended and self._serialization_version not in {3, 4}:
            raise ObjectQueryError(
                "Extended object predicates require serialization version 3"
            )

    def canonical_dict(self) -> dict[str, Any]:
        """Return the complete current semantic descriptor."""

        value = {
            "zones": list(self.zones),
            "owner": self.owner,
            "controller": self.controller,
            "types_all": list(self.types_all),
            "types_any": list(self.types_any),
            "excluded_types": list(self.excluded_types),
            "subtypes_all": list(self.subtypes_all),
            "supertypes_all": list(self.supertypes_all),
            "colors_all": list(self.colors_all),
            "colors_any": list(self.colors_any),
            "keywords_all": list(self.keywords_all),
            "token": self.token,
            "tapped": self.tapped,
            "include_phased_out": self.include_phased_out,
            "known_to_actor": self.known_to_actor,
            "exclude_ref": self.exclude_ref,
        }
        if self._serialization_version == 3:
            value.update(
                {
                    "subtypes_any": list(self.subtypes_any),
                    "excluded_subtypes": list(self.excluded_subtypes),
                    "colorless": self.colorless,
                    "state_predicate": (
                        self.state_predicate.to_dict()
                        if self.state_predicate is not None
                        else None
                    ),
                }
            )
        elif self._serialization_version == 4:
            value.update(
                {
                    "subtypes_any": list(self.subtypes_any),
                    "excluded_subtypes": list(self.excluded_subtypes),
                    "colorless": self.colorless,
                    "state_predicate": (
                        self.state_predicate.to_dict()
                        if self.state_predicate is not None
                        else None
                    ),
                    "excluded_controllers": list(
                        self.excluded_controllers
                    ),
                    "minimum_color_count": self.minimum_color_count,
                }
            )
        return value

    def to_dict(self) -> dict[str, Any]:
        value = self.canonical_dict()
        if self._serialization_version == 1:
            # Historical Game Record v3 payloads predate the additive
            # types-any predicate.  Preserve their exact serialized shape.
            value.pop("types_any")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObjectQuerySpec":
        if not isinstance(value, Mapping):
            raise ObjectQueryError("Object query must be an object")
        actual = frozenset(value)
        if actual not in {
            _QUERY_FIELDS,
            _LEGACY_QUERY_FIELDS,
            _EXTENDED_QUERY_FIELDS,
            _RELATIONAL_QUERY_FIELDS,
        }:
            expected = (
                _RELATIONAL_QUERY_FIELDS
                if actual.intersection(
                    _RELATIONAL_QUERY_FIELDS - _EXTENDED_QUERY_FIELDS
                )
                else _EXTENDED_QUERY_FIELDS
                if actual.intersection(_EXTENDED_QUERY_FIELDS - _QUERY_FIELDS)
                else _QUERY_FIELDS
            )
            missing = sorted(expected - actual)
            unknown = sorted(actual - _RELATIONAL_QUERY_FIELDS)
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unknown " + ", ".join(unknown))
            raise ObjectQueryError(
                "Object query fields: " + "; ".join(details)
            )
        raw_state = value.get("state_predicate")
        state_predicate = (
            PermanentStatePredicateSpec.from_dict(raw_state)
            if raw_state is not None
            else None
        )
        return cls(
            zones=value["zones"],
            owner=value["owner"],
            controller=value["controller"],
            excluded_controllers=value.get("excluded_controllers", ()),
            types_all=value["types_all"],
            types_any=value.get("types_any", ()),
            excluded_types=value["excluded_types"],
            subtypes_all=value["subtypes_all"],
            subtypes_any=value.get("subtypes_any", ()),
            excluded_subtypes=value.get("excluded_subtypes", ()),
            supertypes_all=value["supertypes_all"],
            colors_all=value["colors_all"],
            colors_any=value["colors_any"],
            colorless=value.get("colorless"),
            minimum_color_count=value.get("minimum_color_count"),
            keywords_all=value["keywords_all"],
            token=value["token"],
            tapped=value["tapped"],
            include_phased_out=value["include_phased_out"],
            known_to_actor=value["known_to_actor"],
            exclude_ref=value["exclude_ref"],
            state_predicate=state_predicate,
            _serialization_version=(
                4
                if actual == _RELATIONAL_QUERY_FIELDS
                else 3
                if actual == _EXTENDED_QUERY_FIELDS
                else 2
                if "types_any" in value
                else 1
            ),
        )


def validate_chosen_damage_source_predicate(
    spec: ObjectQuerySpec,
) -> ObjectQuerySpec:
    """Validate the closed public CR 609.7 chosen-source predicate family."""

    if not isinstance(spec, ObjectQuerySpec):
        raise ObjectQueryError(
            "Chosen damage sources require a typed object predicate"
        )
    # Import lazily because damage-source behavior consumes typed ability
    # fragments, while characteristic fragments embed this shared predicate.
    # The runtime dependency is needed only for this specialized validator.
    from .damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES

    if not spec.zones or not set(spec.zones).issubset(
        REPRESENTED_DAMAGE_SOURCE_ZONES
    ):
        raise ObjectQueryError(
            "Chosen damage sources require nonempty represented public zones"
        )
    if spec.known_to_actor is not True:
        raise ObjectQueryError(
            "Chosen damage sources must be legally known to the chooser"
        )
    if spec.include_phased_out:
        raise ObjectQueryError(
            "Chosen damage sources cannot include phased-out objects"
        )
    if spec.excluded_types or spec.excluded_subtypes:
        raise ObjectQueryError(
            "Chosen damage sources do not support excluded types or subtypes"
        )
    if (
        spec.subtypes_any
        or spec.colorless is not None
        or spec.state_predicate is not None
    ):
        raise ObjectQueryError(
            "Chosen damage sources do not support extended public predicates"
        )
    if spec.token is not None or spec.tapped is not None:
        raise ObjectQueryError(
            "Chosen damage sources do not support token or tapped predicates"
        )
    if spec.exclude_ref is not None:
        raise ObjectQueryError(
            "Chosen damage sources do not support unrelated exclusions"
        )
    return spec
