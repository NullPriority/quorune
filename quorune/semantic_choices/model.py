from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

from ..replacement.immutable import FrozenMap, freeze_value, thaw_value


class SemanticChoiceError(ValueError):
    """A semantic-choice request, continuation, or response is malformed."""


Visibility = Literal["public", "actor_private"]
_ORDER_SCHEMA_KEY = "order"


def _require_exact_fields(
    value: Mapping[str, Any],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    label: str,
) -> None:
    required_fields = frozenset(required)
    allowed = required_fields | frozenset(optional)
    missing = sorted(required_fields - value.keys())
    unknown = sorted(value.keys() - allowed)
    if missing:
        raise SemanticChoiceError(
            f"{label} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise SemanticChoiceError(
            f"{label} contains unknown fields: {', '.join(unknown)}"
        )


def _string(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        suffix = "a string" if allow_empty else "a nonempty string"
        raise SemanticChoiceError(f"{field_name} must be {suffix}")
    return value


def _optional_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name=field_name, allow_empty=True)


def _integer(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise SemanticChoiceError(
            f"{field_name} must be an integer of at least {minimum}"
        )
    return value


@dataclass(frozen=True, slots=True)
class ScalarChoice:
    field_name: str
    legal_values: tuple[Any, ...] = ()
    value_type: str | None = None
    optional: bool = False
    nonempty: bool = False
    max_length: int | None = None

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="scalar field_name")
        if not self.legal_values and self.value_type is None:
            raise SemanticChoiceError(
                "A scalar choice requires legal values or a value type"
            )
        if self.max_length is not None:
            _integer(
                self.max_length,
                field_name="scalar max_length",
                minimum=1,
            )
        object.__setattr__(
            self,
            "legal_values",
            tuple(freeze_value(value) for value in self.legal_values),
        )

    def choice_schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {"field": self.field_name}
        if self.legal_values:
            result["legal_values"] = thaw_value(self.legal_values)
        if self.value_type is not None:
            result["type"] = self.value_type
        if self.optional:
            result["optional"] = True
        if self.nonempty:
            result["nonempty"] = True
        if self.max_length is not None:
            result["max_length"] = self.max_length
        return result


@dataclass(frozen=True, slots=True)
class ObjectChoice:
    field_name: str
    legal_refs: tuple[str, ...]
    zones: tuple[str, ...]
    minimum: int = 1
    maximum: int = 1
    optional: bool = False
    distinct: bool = True
    visibility: Visibility = "public"
    owner_relation: str = "any"
    controller_relation: str = "any"
    predicates: FrozenMap = field(default_factory=FrozenMap)
    schema_extras: FrozenMap = field(default_factory=FrozenMap)
    allowed_cardinalities: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="object field_name")
        if not self.zones or any(not zone for zone in self.zones):
            raise SemanticChoiceError("An object choice requires named zones")
        if len(self.legal_refs) != len(set(self.legal_refs)):
            raise SemanticChoiceError("Object choice legal refs must be unique")
        _integer(self.minimum, field_name="object minimum")
        _integer(self.maximum, field_name="object maximum")
        if self.minimum > self.maximum:
            raise SemanticChoiceError("Object choice minimum exceeds maximum")
        if self.maximum > len(self.legal_refs):
            raise SemanticChoiceError(
                "Object choice maximum exceeds the legal-ref count"
            )
        if self.optional and self.minimum != 0:
            raise SemanticChoiceError(
                "An optional object choice must have a zero minimum"
            )
        if self.allowed_cardinalities is not None:
            counts = self.allowed_cardinalities
            if (
                not isinstance(counts, tuple)
                or not counts
                or any(type(count) is not int for count in counts)
                or counts != tuple(sorted(set(counts)))
                or counts[0] != self.minimum
                or counts[-1] != self.maximum
            ):
                raise SemanticChoiceError(
                    "Object choice cardinalities must be sorted unique integers spanning its bounds"
                )
        if self.visibility not in {"public", "actor_private"}:
            raise SemanticChoiceError("Unknown object-choice visibility")
        if not isinstance(self.predicates, FrozenMap):
            object.__setattr__(self, "predicates", FrozenMap(self.predicates))
        if not isinstance(self.schema_extras, FrozenMap):
            object.__setattr__(
                self,
                "schema_extras",
                FrozenMap(self.schema_extras),
            )

    def choice_schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "field": self.field_name,
            "legal_refs": list(self.legal_refs),
        }
        if self.minimum != 1 or self.maximum != 1:
            result.update(
                {
                    "minimum": self.minimum,
                    "maximum": self.maximum,
                    "distinct": self.distinct,
                }
            )
        if self.optional:
            result["optional"] = True
        if self.allowed_cardinalities is not None:
            result["allowed_cardinalities"] = list(self.allowed_cardinalities)
        result.update(thaw_value(self.schema_extras))
        return result


@dataclass(frozen=True, slots=True)
class TargetAssignmentChoice:
    target_schema: FrozenMap
    default_targets: tuple[str, ...] = ()
    optional: bool = True
    may_retain_each_default: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target_schema, FrozenMap):
            object.__setattr__(
                self,
                "target_schema",
                FrozenMap(self.target_schema),
            )

    def choice_schema(self) -> dict[str, Any]:
        return {
            "field": "targets",
            "optional": self.optional,
            "default": list(self.default_targets),
            "target_schema": thaw_value(self.target_schema),
            **({"may_retain_each_default": True} if self.may_retain_each_default else {}),
        }


@dataclass(frozen=True, slots=True)
class OrderingChoice:
    field_name: str
    legal_refs: tuple[str, ...]
    complete_permutation: bool = True
    visibility: Visibility = "public"
    schema_extras: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="ordering field_name")
        if len(self.legal_refs) != len(set(self.legal_refs)):
            raise SemanticChoiceError("Ordering refs must be unique")
        if self.visibility not in {"public", "actor_private"}:
            raise SemanticChoiceError("Unknown ordering visibility")
        if not isinstance(self.schema_extras, FrozenMap):
            object.__setattr__(
                self,
                "schema_extras",
                FrozenMap(self.schema_extras),
            )

    def choice_schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "field": self.field_name,
            "legal_refs": list(self.legal_refs),
            "distinct": True,
        }
        if self.complete_permutation:
            result["minimum"] = len(self.legal_refs)
            result["maximum"] = len(self.legal_refs)
        result.update(thaw_value(self.schema_extras))
        return result


@dataclass(frozen=True, slots=True)
class LibraryPartitionChoice:
    """Order one private set into two complete destination groups."""

    field_name: str
    legal_refs: tuple[str, ...]
    visibility: Visibility = "actor_private"
    partitions: FrozenMap = field(
        default_factory=lambda: FrozenMap(
            {
                "top": {
                    _ORDER_SCHEMA_KEY: "top_to_bottom",
                    "label": "Top of library",
                },
                "bottom": {
                    _ORDER_SCHEMA_KEY: "bottom_to_top",
                    "label": "Bottom of library",
                },
            }
        )
    )
    legacy_destination: str | None = "library_bottom"

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="library partition field_name")
        if not self.legal_refs:
            raise SemanticChoiceError(
                "A library partition choice requires at least one reference"
            )
        if len(self.legal_refs) != len(set(self.legal_refs)) or any(
            not ref for ref in self.legal_refs
        ):
            raise SemanticChoiceError(
                "Library partition refs must be nonempty and unique"
            )
        if self.visibility not in {"public", "actor_private"}:
            raise SemanticChoiceError("Unknown library-partition visibility")
        if not isinstance(self.partitions, FrozenMap):
            object.__setattr__(self, "partitions", FrozenMap(self.partitions))
        if len(self.partitions) != 2:
            raise SemanticChoiceError(
                "A library partition choice requires exactly two groups"
            )
        for name, descriptor in self.partitions.items():
            _string(name, field_name="library partition group")
            if not isinstance(descriptor, Mapping) or not isinstance(
                descriptor.get(_ORDER_SCHEMA_KEY), str
            ):
                raise SemanticChoiceError(
                    "Library partition groups require an ordering"
                )
        if self.legacy_destination is not None:
            _string(
                self.legacy_destination,
                field_name="library partition legacy destination",
            )

    def choice_schema(self) -> dict[str, Any]:
        result = {
            "field": self.field_name,
            "shape": "ordered_partition",
            "legal_refs": list(self.legal_refs),
            "partitions": thaw_value(self.partitions),
            "complete": True,
            "distinct": True,
        }
        if self.legacy_destination is not None:
            result["destination"] = self.legacy_destination
        return result


@dataclass(frozen=True, slots=True)
class SearchChoice:
    field_name: str
    legal_refs: tuple[str, ...]
    searched_zone: str
    destination: str
    minimum: int
    maximum: int
    may_fail_to_find: bool
    reveal: bool
    shuffle: bool
    predicates: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="search field_name")
        _string(self.searched_zone, field_name="searched_zone")
        _string(self.destination, field_name="search destination")
        _integer(self.minimum, field_name="search minimum")
        _integer(self.maximum, field_name="search maximum")
        if self.minimum > self.maximum or self.maximum > len(self.legal_refs):
            raise SemanticChoiceError("Invalid search selection bounds")
        if not isinstance(self.predicates, FrozenMap):
            object.__setattr__(self, "predicates", FrozenMap(self.predicates))


@dataclass(frozen=True, slots=True)
class DistributionChoice:
    field_name: str
    legal_refs: tuple[str, ...]
    exact_total: int
    minimum_each: int = 0
    maximum_each: int | None = None

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="distribution field_name")
        _integer(self.exact_total, field_name="distribution total")
        _integer(self.minimum_each, field_name="distribution minimum")
        if self.maximum_each is not None:
            _integer(
                self.maximum_each,
                field_name="distribution maximum",
            )
            if self.minimum_each > self.maximum_each:
                raise SemanticChoiceError(
                    "Distribution minimum exceeds maximum"
                )


@dataclass(frozen=True, slots=True)
class DecisionMapChoice:
    field_name: str
    legal_refs: tuple[str, ...]
    required: int
    legal_values: tuple[str, ...]
    companion_schema: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="decision-map field_name")
        _integer(self.required, field_name="decision-map required")
        if self.required > len(self.legal_refs):
            raise SemanticChoiceError(
                "Decision-map required count exceeds legal refs"
            )
        if not self.legal_values:
            raise SemanticChoiceError(
                "Decision-map choices require legal values"
            )
        if not isinstance(self.companion_schema, FrozenMap):
            object.__setattr__(
                self,
                "companion_schema",
                FrozenMap(self.companion_schema),
            )

    def choice_schema(self) -> dict[str, Any]:
        result = {
            "field": self.field_name,
            "shape": "object_map",
            "legal_refs": list(self.legal_refs),
            "required": self.required,
            "legal_values": list(self.legal_values),
        }
        result.update(thaw_value(self.companion_schema))
        return result


@dataclass(frozen=True, slots=True)
class ReferenceSetChoice:
    field_name: str
    legal_refs: tuple[str, ...]
    minimum: int = 0
    maximum: int | None = None
    distinct: bool = True

    def __post_init__(self) -> None:
        _string(self.field_name, field_name="reference-set field_name")
        if len(self.legal_refs) != len(set(self.legal_refs)):
            raise SemanticChoiceError("Reference-set legal refs must be unique")
        _integer(self.minimum, field_name="reference-set minimum")
        maximum = len(self.legal_refs) if self.maximum is None else self.maximum
        _integer(maximum, field_name="reference-set maximum")
        if self.minimum > maximum or maximum > len(self.legal_refs):
            raise SemanticChoiceError("Invalid reference-set bounds")
        object.__setattr__(self, "maximum", maximum)

    def choice_schema(self) -> dict[str, Any]:
        return {
            "field": self.field_name,
            "legal_refs": list(self.legal_refs),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "distinct": self.distinct,
        }


ChoiceModel = (
    ScalarChoice
    | ObjectChoice
    | TargetAssignmentChoice
    | OrderingChoice
    | LibraryPartitionChoice
    | SearchChoice
    | DistributionChoice
    | DecisionMapChoice
    | ReferenceSetChoice
)


@dataclass(frozen=True, slots=True)
class SemanticChoiceRequest:
    prompt: str
    choice: ChoiceModel
    public_context: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        _string(self.prompt, field_name="choice prompt")
        if not isinstance(self.public_context, FrozenMap):
            object.__setattr__(
                self,
                "public_context",
                FrozenMap(self.public_context),
            )

    def payload(self) -> dict[str, Any]:
        context = thaw_value(self.public_context)
        schema = self.choice.choice_schema()
        context.update(
            {
                "prompt": self.prompt,
                "legal_actions": [
                    {
                        "id": "choose",
                        "action": "choose",
                        "choice_schema": schema,
                    }
                ],
            }
        )
        return context


@dataclass(frozen=True, slots=True)
class SemanticChoiceFrame:
    semantic_program_id: str
    semantic_program_version: int | None
    stack_object: str
    instruction_pointer: int
    controller: str
    locals: FrozenMap = field(default_factory=FrozenMap)
    pending_choice_id: str | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise SemanticChoiceError("Unsupported semantic frame version")
        _string(
            self.semantic_program_id,
            field_name="semantic_program_id",
            allow_empty=True,
        )
        _string(self.stack_object, field_name="stack_object")
        _integer(
            self.instruction_pointer,
            field_name="instruction_pointer",
        )
        _string(self.controller, field_name="controller")
        if self.semantic_program_version is not None:
            _integer(
                self.semantic_program_version,
                field_name="semantic_program_version",
                minimum=1,
            )
        if not isinstance(self.locals, FrozenMap):
            object.__setattr__(self, "locals", FrozenMap(self.locals))

    def with_pending_choice(self, decision_id: str) -> "SemanticChoiceFrame":
        return SemanticChoiceFrame(
            semantic_program_id=self.semantic_program_id,
            semantic_program_version=self.semantic_program_version,
            stack_object=self.stack_object,
            instruction_pointer=self.instruction_pointer,
            controller=self.controller,
            locals=self.locals,
            pending_choice_id=_string(
                decision_id,
                field_name="pending_choice_id",
            ),
            schema_version=self.schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "semantic_program_id": self.semantic_program_id,
            "semantic_program_version": self.semantic_program_version,
            "stack_object": self.stack_object,
            "instruction_pointer": self.instruction_pointer,
            "locals": thaw_value(self.locals),
            "controller": self.controller,
            "pending_choice_id": self.pending_choice_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticChoiceFrame":
        _require_exact_fields(
            value,
            required=(
                "schema_version",
                "semantic_program_id",
                "semantic_program_version",
                "stack_object",
                "instruction_pointer",
                "locals",
                "controller",
                "pending_choice_id",
            ),
            label="semantic frame",
        )
        locals_value = value["locals"]
        if not isinstance(locals_value, Mapping):
            raise SemanticChoiceError("semantic frame locals must be a mapping")
        return cls(
            schema_version=_integer(
                value["schema_version"],
                field_name="semantic frame schema_version",
                minimum=1,
            ),
            semantic_program_id=_string(
                value["semantic_program_id"],
                field_name="semantic_program_id",
                allow_empty=True,
            ),
            semantic_program_version=value["semantic_program_version"],
            stack_object=_string(
                value["stack_object"],
                field_name="stack_object",
            ),
            instruction_pointer=_integer(
                value["instruction_pointer"],
                field_name="instruction_pointer",
            ),
            locals=FrozenMap(locals_value),
            controller=_string(value["controller"], field_name="controller"),
            pending_choice_id=_optional_string(
                value["pending_choice_id"],
                field_name="pending_choice_id",
            ),
        )


@dataclass(frozen=True, slots=True)
class SemanticChoiceContinuation:
    handler_id: str
    handler_version: int
    stack_ref: str
    effect: FrozenMap
    remaining: tuple[FrozenMap, ...]
    destination: str | None
    note: str
    semantic_frame: SemanticChoiceFrame
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise SemanticChoiceError(
                "New semantic choice continuations require schema version 2"
            )
        _string(self.handler_id, field_name="handler_id")
        _integer(self.handler_version, field_name="handler_version", minimum=1)
        _string(self.stack_ref, field_name="stack_ref")
        if not isinstance(self.effect, FrozenMap):
            object.__setattr__(self, "effect", FrozenMap(self.effect))
        frozen_remaining: list[FrozenMap] = []
        for index, effect in enumerate(self.remaining):
            if not isinstance(effect, Mapping):
                raise SemanticChoiceError(
                    f"remaining[{index}] must be a mapping"
                )
            frozen_remaining.append(
                effect if isinstance(effect, FrozenMap) else FrozenMap(effect)
            )
        object.__setattr__(self, "remaining", tuple(frozen_remaining))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "handler_id": self.handler_id,
            "handler_version": self.handler_version,
            "stack_ref": self.stack_ref,
            "effect": thaw_value(self.effect),
            "remaining": [thaw_value(effect) for effect in self.remaining],
            "destination": self.destination,
            "note": self.note,
            "semantic_frame": self.semantic_frame.to_dict(),
        }

    def with_pending_choice(self, decision_id: str) -> "SemanticChoiceContinuation":
        return SemanticChoiceContinuation(
            handler_id=self.handler_id,
            handler_version=self.handler_version,
            stack_ref=self.stack_ref,
            effect=self.effect,
            remaining=self.remaining,
            destination=self.destination,
            note=self.note,
            semantic_frame=self.semantic_frame.with_pending_choice(decision_id),
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        legacy_handler_id: str | None = None,
        legacy_handler_version: int | None = None,
    ) -> "SemanticChoiceContinuation":
        if value.get("schema_version") == 2:
            _require_exact_fields(
                value,
                required=(
                    "schema_version",
                    "handler_id",
                    "handler_version",
                    "stack_ref",
                    "effect",
                    "remaining",
                    "destination",
                    "note",
                    "semantic_frame",
                ),
                label="semantic choice continuation",
            )
            handler_id = _string(value["handler_id"], field_name="handler_id")
            handler_version = _integer(
                value["handler_version"],
                field_name="handler_version",
                minimum=1,
            )
        else:
            _require_exact_fields(
                value,
                required=(
                    "stack_ref",
                    "effect",
                    "remaining",
                    "destination",
                    "note",
                    "semantic_frame",
                ),
                label="legacy semantic choice continuation",
            )
            if legacy_handler_id is None or legacy_handler_version is None:
                raise SemanticChoiceError(
                    "Legacy continuation decoding requires a pinned handler"
                )
            handler_id = legacy_handler_id
            handler_version = legacy_handler_version
        effect = value["effect"]
        remaining = value["remaining"]
        frame = value["semantic_frame"]
        if not isinstance(effect, Mapping):
            raise SemanticChoiceError("continuation effect must be a mapping")
        if not isinstance(remaining, list) or any(
            not isinstance(item, Mapping) for item in remaining
        ):
            raise SemanticChoiceError(
                "continuation remaining effects must be a list of mappings"
            )
        if not isinstance(frame, Mapping):
            raise SemanticChoiceError(
                "continuation semantic_frame must be a mapping"
            )
        return cls(
            handler_id=handler_id,
            handler_version=handler_version,
            stack_ref=_string(value["stack_ref"], field_name="stack_ref"),
            effect=FrozenMap(effect),
            remaining=tuple(FrozenMap(item) for item in remaining),
            destination=_optional_string(
                value["destination"],
                field_name="destination",
            ),
            note=_string(value["note"], field_name="note", allow_empty=True),
            semantic_frame=SemanticChoiceFrame.from_dict(frame),
        )


@dataclass(frozen=True, slots=True)
class AutoContinue:
    reason: str
    prepend_effects: tuple[FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        _string(self.reason, field_name="auto-continue reason")
        object.__setattr__(
            self,
            "prepend_effects",
            tuple(
                effect
                if isinstance(effect, FrozenMap)
                else FrozenMap(effect)
                for effect in self.prepend_effects
            ),
        )


@dataclass(frozen=True, slots=True)
class SemanticChoicePreparation:
    request: SemanticChoiceRequest | None
    continuation_effect: FrozenMap
    preparation_intents: tuple[Any, ...] = ()
    auto_continue: AutoContinue | None = None

    def __post_init__(self) -> None:
        if (self.request is None) == (self.auto_continue is None):
            raise SemanticChoiceError(
                "Preparation must issue a request or explicitly auto-continue"
            )
        if not isinstance(self.continuation_effect, FrozenMap):
            object.__setattr__(
                self,
                "continuation_effect",
                FrozenMap(self.continuation_effect),
            )


@dataclass(frozen=True, slots=True)
class SemanticChoiceCompletion:
    intents: tuple[Any, ...] = ()
    prepend_effects: tuple[FrozenMap, ...] = ()
    repeat_effect: FrozenMap | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "prepend_effects",
            tuple(
                effect
                if isinstance(effect, FrozenMap)
                else FrozenMap(effect)
                for effect in self.prepend_effects
            ),
        )
        if self.repeat_effect is not None and not isinstance(
            self.repeat_effect, FrozenMap
        ):
            object.__setattr__(
                self,
                "repeat_effect",
                FrozenMap(self.repeat_effect),
            )


@dataclass(frozen=True, slots=True)
class SemanticChoiceResult:
    issued_decision_id: str | None = None
    auto_continued: bool = False

    def __post_init__(self) -> None:
        if bool(self.issued_decision_id) == self.auto_continued:
            raise SemanticChoiceError(
                "A choice result must issue one decision or auto-continue"
            )
