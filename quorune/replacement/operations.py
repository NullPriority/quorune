from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TypeAlias

from .immutable import FrozenMap, freeze_value, thaw_value


OPERATION_SCHEMA_VERSION = 1
_DREDGE_LABEL = "Dredge "


class ReplacementOperationError(ValueError):
    pass


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, operation: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ReplacementOperationError(
            f"Replacement {operation} fields: {'; '.join(details)}"
        )


def _field(value: Any, *, operation: str) -> str:
    result = str(value or "")
    if not result:
        raise ReplacementOperationError(
            f"Replacement {operation} requires a field"
        )
    return result


def _integer(value: Any, *, field: str, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        qualifier = f" at least {minimum}" if minimum is not None else ""
        raise ReplacementOperationError(
            f"Replacement {field} must be an integer{qualifier}"
        )
    return value


@dataclass(frozen=True, slots=True)
class SetField:
    field: str
    value: Any
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _field(self.field, operation="set"))
        object.__setattr__(self, "value", freeze_value(self.value))

    def to_dict(self) -> dict[str, Any]:
        return {"op": "set", "field": self.field, "value": thaw_value(self.value)}


@dataclass(frozen=True, slots=True)
class AddAmount:
    field: str
    amount: int
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _field(self.field, operation="add"))
        _integer(self.amount, field="add amount")

    def to_dict(self) -> dict[str, Any]:
        return {"op": "add", "field": self.field, "amount": self.amount}


@dataclass(frozen=True, slots=True)
class MultiplyAmount:
    field: str
    factor: int
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "field", _field(self.field, operation="multiply")
        )
        _integer(self.factor, field="multiply factor", minimum=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "multiply",
            "field": self.field,
            "factor": self.factor,
        }


@dataclass(frozen=True, slots=True)
class PreventAmount:
    amount: int | None = None
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.amount is not None:
            _integer(self.amount, field="prevent amount", minimum=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "prevent",
            **({"amount": self.amount} if self.amount is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class PreventUsingShield:
    """Consume a durable prevention resource at a batch boundary.

    The operation deliberately contains no mutable state. ``remaining`` is a
    replay-pinned snapshot used to validate the eventual commit plan; the
    authoritative shield is changed only after the complete damage batch has
    validated.
    """

    shield_id: str
    remaining: int | None
    consume_on_application: bool = True
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        shield_id = str(self.shield_id or "")
        if not shield_id:
            raise ReplacementOperationError(
                "Shield prevention requires a stable shield ID"
            )
        if self.remaining is not None:
            _integer(
                self.remaining,
                field="shield remaining amount",
                minimum=1,
            )
        if type(self.consume_on_application) is not bool:
            raise ReplacementOperationError(
                "Shield consumption policy must be a boolean"
            )
        object.__setattr__(self, "shield_id", shield_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "prevent_using_shield",
            "shield_id": self.shield_id,
            "remaining": self.remaining,
            "consume_on_application": self.consume_on_application,
        }


@dataclass(frozen=True, slots=True)
class RedirectDamage:
    """Replace one damage recipient with a complete public snapshot."""

    target: str
    target_kind: str
    target_controller: str
    target_object_id: str | None = None
    target_logical_object_id: str | None = None
    target_owner: str | None = None
    target_types: tuple[str, ...] = ()
    target_subtypes: tuple[str, ...] = ()
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        target = str(self.target or "")
        target_kind = str(self.target_kind or "")
        controller = str(self.target_controller or "")
        if not target or not controller or target_kind not in {
            "player",
            "permanent",
        }:
            raise ReplacementOperationError(
                "Damage redirection requires a player or permanent snapshot"
            )
        object_id = str(self.target_object_id or "") or None
        logical_id = str(self.target_logical_object_id or "") or None
        owner = str(self.target_owner or "") or None
        if target_kind == "player":
            if any(value is not None for value in (object_id, logical_id, owner)):
                raise ReplacementOperationError(
                    "A redirected player cannot carry object identity"
                )
        elif not all((object_id, logical_id, owner)):
            raise ReplacementOperationError(
                "A redirected permanent requires complete object identity"
            )
        types = tuple(sorted({str(value) for value in self.target_types if str(value)}))
        subtypes = tuple(
            sorted({str(value) for value in self.target_subtypes if str(value)})
        )
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "target_kind", target_kind)
        object.__setattr__(self, "target_controller", controller)
        object.__setattr__(self, "target_object_id", object_id)
        object.__setattr__(self, "target_logical_object_id", logical_id)
        object.__setattr__(self, "target_owner", owner)
        object.__setattr__(self, "target_types", types)
        object.__setattr__(self, "target_subtypes", subtypes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "redirect_damage",
            "target": self.target,
            "target_kind": self.target_kind,
            "target_controller": self.target_controller,
            "target_object_id": self.target_object_id,
            "target_logical_object_id": self.target_logical_object_id,
            "target_owner": self.target_owner,
            "target_types": list(self.target_types),
            "target_subtypes": list(self.target_subtypes),
        }


@dataclass(frozen=True, slots=True)
class AppendValues:
    field: str
    values: tuple[Any, ...]
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "field", _field(self.field, operation="append")
        )
        if not isinstance(self.values, (list, tuple)):
            raise ReplacementOperationError(
                "Replacement append values must be an array"
            )
        object.__setattr__(
            self,
            "values",
            tuple(freeze_value(value) for value in self.values),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "append",
            "field": self.field,
            "values": [thaw_value(value) for value in self.values],
        }


@dataclass(frozen=True, slots=True)
class UnionValues:
    field: str
    values: tuple[Any, ...]
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "field", _field(self.field, operation="union")
        )
        if not isinstance(self.values, (list, tuple)):
            raise ReplacementOperationError(
                "Replacement union values must be an array"
            )
        object.__setattr__(
            self,
            "values",
            tuple(freeze_value(value) for value in self.values),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "union",
            "field": self.field,
            "values": [thaw_value(value) for value in self.values],
        }


@dataclass(frozen=True, slots=True)
class CreateNestedEvent:
    event: FrozenMap
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.event, Mapping):
            raise ReplacementOperationError(
                "Nested replacement operation requires an event object"
            )
        object.__setattr__(self, "event", FrozenMap(self.event))

    def to_dict(self) -> dict[str, Any]:
        return {"op": "nested_event", "event": thaw_value(self.event)}


@dataclass(frozen=True, slots=True)
class CreateAffectedObjectCounter:
    """Create a counter-placement child for a zone event's affected object.

    Unlike ``CreateNestedEvent``, this operation carries no object-specific
    state.  One immutable replacement effect can therefore participate in a
    simultaneous batch without being duplicated or rebound per object.
    """

    counter_name: str
    amount: int
    placing_player: str
    source_ref: str
    sequence: int = 0
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.counter_name) is not str:
            raise ReplacementOperationError(
                "Affected-object counter name must be a string"
            )
        normalized = " ".join(self.counter_name.casefold().split())
        if not normalized:
            raise ReplacementOperationError(
                "Affected-object counters require a counter name"
            )
        object.__setattr__(self, "counter_name", normalized)
        _integer(self.amount, field="affected-object counter amount", minimum=1)
        for field_name in ("placing_player", "source_ref"):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise ReplacementOperationError(
                    f"Affected-object counters require string {field_name}"
                )
        _integer(self.sequence, field="affected-object counter sequence", minimum=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "create_affected_object_counter",
            "counter_name": self.counter_name,
            "amount": self.amount,
            "placing_player": self.placing_player,
            "source_ref": self.source_ref,
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class GrantAffectedObjectKeyword:
    """Grant one closed keyword to the affected entering zone object."""

    keyword: str
    sequence: int = 0
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.keyword) is not str:
            raise ReplacementOperationError(
                "Affected-object keyword must be a string"
            )
        keyword = " ".join(self.keyword.casefold().split())
        if keyword not in {
            "flying",
            "first strike",
            "haste",
            "trample",
            "vigilance",
        }:
            raise ReplacementOperationError(
                "Affected-object keyword is outside the represented vocabulary"
            )
        object.__setattr__(self, "keyword", keyword)
        _integer(
            self.sequence,
            field="affected-object keyword sequence",
            minimum=0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "grant_affected_object_keyword",
            "keyword": self.keyword,
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class CreateAdditionalToken:
    """Add one fixed token specification to a token-creation event.

    The operation is an immutable replacement-event transformation.  It does
    not create a permanent or inspect mutable game state; the authoritative
    token-creation owner commits the transformed specification only after the
    complete replacement batch has resolved.
    """

    name: str
    quantity: int
    characteristics: FrozenMap
    card_types: tuple[str, ...]
    subtypes: tuple[str, ...]
    handler_id: str
    source_ref: str
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("name", "handler_id", "source_ref"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ReplacementOperationError(
                    f"Additional tokens require nonempty {field_name}"
                )
            object.__setattr__(self, field_name, value.strip())
        _integer(self.quantity, field="additional token quantity", minimum=1)
        if not isinstance(self.characteristics, Mapping):
            raise ReplacementOperationError(
                "Additional token characteristics must be an object"
            )
        object.__setattr__(
            self,
            "characteristics",
            FrozenMap(self.characteristics),
        )
        for field_name in ("card_types", "subtypes"):
            supplied = getattr(self, field_name)
            if not isinstance(supplied, (list, tuple)) or any(
                type(value) is not str or not value.strip()
                for value in supplied
            ):
                raise ReplacementOperationError(
                    f"Additional token {field_name} must be nonempty strings"
                )
            normalized = tuple(
                sorted({value.casefold().strip() for value in supplied})
            )
            object.__setattr__(self, field_name, normalized)
        if not self.card_types:
            raise ReplacementOperationError(
                "Additional tokens require at least one card type"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "create_additional_token",
            "name": self.name,
            "quantity": self.quantity,
            "characteristics": thaw_value(self.characteristics),
            "card_types": list(self.card_types),
            "subtypes": list(self.subtypes),
            "handler_id": self.handler_id,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class MultiplyTokenCreation:
    """Copy every current token specification within one creation event.

    The immutable specifications, including any association used to choose an
    attacking destination, are copied before the authoritative token owner
    allocates identities or commits permanents.
    """

    factor: int
    handler_id: str
    source_ref: str
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _integer(self.factor, field="token multiplier factor", minimum=2)
        for field_name in ("handler_id", "source_ref"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ReplacementOperationError(
                    f"Token multiplication requires nonempty {field_name}"
                )
            object.__setattr__(self, field_name, value.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "multiply_token_creation",
            "factor": self.factor,
            "handler_id": self.handler_id,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class ReserveZoneChange:
    objects: tuple[str, ...] = ()
    from_field: str | None = None
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        values = tuple(str(value) for value in self.objects)
        if any(not value for value in values):
            raise ReplacementOperationError(
                "Zone-change reservation requires stable object IDs"
            )
        field = str(self.from_field or "") or None
        if bool(values) == bool(field):
            raise ReplacementOperationError(
                "Zone-change reservation requires exactly one object source"
            )
        object.__setattr__(self, "objects", values)
        object.__setattr__(self, "from_field", field)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "reserve_zone_change",
            **(
                {"objects": list(self.objects)}
                if self.objects
                else {"from_field": self.from_field}
            ),
        }


@dataclass(frozen=True, slots=True)
class CapResultLifeLoss:
    minimum: int
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _integer(self.minimum, field="result life floor minimum")

    def to_dict(self) -> dict[str, Any]:
        return {"op": "cap_result_life_loss", "minimum": self.minimum}


@dataclass(frozen=True, slots=True)
class PreventDraw:
    """Replace one card-draw event with no draw result."""

    schema_version: int = OPERATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"op": "prevent_draw"}


@dataclass(frozen=True, slots=True)
class CreateResultDraws:
    """Replace one draw with a new fixed-count draw instruction.

    The application layer binds the affected player and the producing effect
    identity.  Keeping those runtime facts out of the descriptor prevents a
    component from forging a chooser or bypassing CR 614.5.
    """

    count: int
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _integer(self.count, field="result draw count", minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return {"op": "create_result_draws", "count": self.count}


@dataclass(frozen=True, slots=True)
class DredgeDraw:
    """Replace one draw with the closed CR 702.52 Dredge result."""

    source_ref: str
    source_object_id: str
    source_zone_change_counter: int
    mill_count: int
    schema_version: int = OPERATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_ref", self.source_ref),
            ("source_object_id", self.source_object_id),
        ):
            if type(value) is not str or not value:
                raise ReplacementOperationError(
                    f"{_DREDGE_LABEL}{field_name} must be a nonempty string"
                )
        _integer(
            self.source_zone_change_counter,
            field="Dredge source zone-change counter",
            minimum=0,
        )
        _integer(self.mill_count, field="Dredge mill count", minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "dredge_draw",
            "source_ref": self.source_ref,
            "source_object_id": self.source_object_id,
            "source_zone_change_counter": self.source_zone_change_counter,
            "mill_count": self.mill_count,
        }


ReplacementOperation: TypeAlias = (
    SetField
    | AddAmount
    | MultiplyAmount
    | PreventAmount
    | PreventUsingShield
    | RedirectDamage
    | AppendValues
    | UnionValues
    | CreateNestedEvent
    | CreateAffectedObjectCounter
    | GrantAffectedObjectKeyword
    | CreateAdditionalToken
    | MultiplyTokenCreation
    | ReserveZoneChange
    | CapResultLifeLoss
    | PreventDraw
    | CreateResultDraws
    | DredgeDraw
)


_TYPED_OPERATION_TYPES = (
    SetField,
    AddAmount,
    MultiplyAmount,
    PreventAmount,
    PreventUsingShield,
    RedirectDamage,
    AppendValues,
    UnionValues,
    CreateNestedEvent,
    CreateAffectedObjectCounter,
    GrantAffectedObjectKeyword,
    CreateAdditionalToken,
    MultiplyTokenCreation,
    ReserveZoneChange,
    CapResultLifeLoss,
    PreventDraw,
    CreateResultDraws,
    DredgeDraw,
)


def _dredge_draw_from_dict(
    value: Mapping[str, Any],
    *,
    operation: str,
) -> DredgeDraw:
    _exact_fields(
        value,
        {
            "op",
            "source_ref",
            "source_object_id",
            "source_zone_change_counter",
            "mill_count",
        },
        operation=operation,
    )
    source_ref = value["source_ref"]
    source_object_id = value["source_object_id"]
    if type(source_ref) is not str or not source_ref:
        raise ReplacementOperationError(
            "Dredge source_ref must be a nonempty string"
        )
    if type(source_object_id) is not str or not source_object_id:
        raise ReplacementOperationError(
            "Dredge source_object_id must be a nonempty string"
        )
    return DredgeDraw(
        source_ref=source_ref,
        source_object_id=source_object_id,
        source_zone_change_counter=_integer(
            value["source_zone_change_counter"],
            field="Dredge source zone-change counter",
            minimum=0,
        ),
        mill_count=_integer(
            value["mill_count"],
            field="Dredge mill count",
            minimum=1,
        ),
    )


def _affected_object_counter_from_dict(
    value: Mapping[str, Any],
    *,
    operation: str,
) -> CreateAffectedObjectCounter:
    _exact_fields(
        value,
        {
            "op",
            "counter_name",
            "amount",
            "placing_player",
            "source_ref",
            "sequence",
        },
        operation=operation,
    )
    return CreateAffectedObjectCounter(
        counter_name=value["counter_name"],
        amount=_integer(
            value["amount"],
            field="affected-object counter amount",
            minimum=1,
        ),
        placing_player=value["placing_player"],
        source_ref=value["source_ref"],
        sequence=_integer(
            value["sequence"],
            field="affected-object counter sequence",
            minimum=0,
        ),
    )


def _affected_object_keyword_from_dict(
    value: Mapping[str, Any],
    *,
    operation: str,
) -> GrantAffectedObjectKeyword:
    _exact_fields(
        value,
        {"op", "keyword", "sequence"},
        operation=operation,
    )
    return GrantAffectedObjectKeyword(
        keyword=value["keyword"],
        sequence=_integer(
            value["sequence"],
            field="affected-object keyword sequence",
            minimum=0,
        ),
    )


def _additional_token_from_dict(
    value: Mapping[str, Any],
    *,
    operation: str,
) -> CreateAdditionalToken:
    _exact_fields(
        value,
        {
            "op",
            "name",
            "quantity",
            "characteristics",
            "card_types",
            "subtypes",
            "handler_id",
            "source_ref",
        },
        operation=operation,
    )
    characteristics = value["characteristics"]
    card_types = value["card_types"]
    subtypes = value["subtypes"]
    if not isinstance(characteristics, Mapping):
        raise ReplacementOperationError(
            "Additional token characteristics must be an object"
        )
    if not isinstance(card_types, (list, tuple)) or not isinstance(
        subtypes, (list, tuple)
    ):
        raise ReplacementOperationError(
            "Additional token type fields must be arrays"
        )
    return CreateAdditionalToken(
        name=value["name"],
        quantity=_integer(
            value["quantity"],
            field="additional token quantity",
            minimum=1,
        ),
        characteristics=FrozenMap(characteristics),
        card_types=tuple(card_types),
        subtypes=tuple(subtypes),
        handler_id=value["handler_id"],
        source_ref=value["source_ref"],
    )


def _token_multiplier_from_dict(
    value: Mapping[str, Any],
    *,
    operation: str,
) -> MultiplyTokenCreation:
    _exact_fields(
        value,
        {"op", "factor", "handler_id", "source_ref"},
        operation=operation,
    )
    return MultiplyTokenCreation(
        factor=_integer(
            value["factor"],
            field="token multiplier factor",
            minimum=2,
        ),
        handler_id=value["handler_id"],
        source_ref=value["source_ref"],
    )


def _redirect_damage_from_dict(
    value: Mapping[str, Any],
    *,
    operation: str,
) -> RedirectDamage:
    _exact_fields(
        value,
        {
            "op",
            "target",
            "target_kind",
            "target_controller",
            "target_object_id",
            "target_logical_object_id",
            "target_owner",
            "target_types",
            "target_subtypes",
        },
        operation=operation,
    )
    target_types = value["target_types"]
    target_subtypes = value["target_subtypes"]
    if not isinstance(target_types, (list, tuple)) or not isinstance(
        target_subtypes, (list, tuple)
    ):
        raise ReplacementOperationError(
            "Damage redirection type fields must be arrays"
        )
    return RedirectDamage(
        target=str(value["target"] or ""),
        target_kind=str(value["target_kind"] or ""),
        target_controller=str(value["target_controller"] or ""),
        target_object_id=(
            str(value["target_object_id"])
            if value["target_object_id"] is not None
            else None
        ),
        target_logical_object_id=(
            str(value["target_logical_object_id"])
            if value["target_logical_object_id"] is not None
            else None
        ),
        target_owner=(
            str(value["target_owner"])
            if value["target_owner"] is not None
            else None
        ),
        target_types=tuple(str(item) for item in target_types),
        target_subtypes=tuple(str(item) for item in target_subtypes),
    )


def operation_from_dict(value: Mapping[str, Any]) -> ReplacementOperation:
    if not isinstance(value, Mapping):
        raise ReplacementOperationError(
            "Replacement operations must be objects"
        )
    op = str(value.get("op") or "")
    if op == "set":
        _exact_fields(value, {"op", "field", "value"}, operation=op)
        return SetField(_field(value["field"], operation=op), value["value"])
    if op == "add":
        _exact_fields(value, {"op", "field", "amount"}, operation=op)
        return AddAmount(
            _field(value["field"], operation=op),
            _integer(value["amount"], field="add amount"),
        )
    if op == "multiply":
        _exact_fields(value, {"op", "field", "factor"}, operation=op)
        return MultiplyAmount(
            _field(value["field"], operation=op),
            _integer(value["factor"], field="multiply factor", minimum=0),
        )
    if op == "prevent":
        expected = {"op", "amount"} if "amount" in value else {"op"}
        _exact_fields(value, expected, operation=op)
        return PreventAmount(
            _integer(value["amount"], field="prevent amount", minimum=0)
            if "amount" in value
            else None
        )
    if op == "prevent_using_shield":
        _exact_fields(
            value,
            {"op", "shield_id", "remaining", "consume_on_application"},
            operation=op,
        )
        remaining = value["remaining"]
        if remaining is not None:
            remaining = _integer(
                remaining,
                field="shield remaining amount",
                minimum=1,
            )
        if type(value["consume_on_application"]) is not bool:
            raise ReplacementOperationError(
                "Shield consumption policy must be a boolean"
            )
        return PreventUsingShield(
            shield_id=str(value["shield_id"] or ""),
            remaining=remaining,
            consume_on_application=value["consume_on_application"],
        )
    if op == "redirect_damage":
        return _redirect_damage_from_dict(value, operation=op)
    if op in {"append", "union"}:
        _exact_fields(value, {"op", "field", "values"}, operation=op)
        values = value["values"]
        if not isinstance(values, (list, tuple)):
            raise ReplacementOperationError(
                f"Replacement {op} values must be an array"
            )
        operation_type = AppendValues if op == "append" else UnionValues
        return operation_type(
            _field(value["field"], operation=op), tuple(values)
        )
    if op == "nested_event":
        _exact_fields(value, {"op", "event"}, operation=op)
        event = value["event"]
        if not isinstance(event, Mapping):
            raise ReplacementOperationError(
                "Nested replacement operation requires an event object"
            )
        return CreateNestedEvent(FrozenMap(event))
    if op == "create_affected_object_counter":
        return _affected_object_counter_from_dict(value, operation=op)
    if op == "grant_affected_object_keyword":
        return _affected_object_keyword_from_dict(value, operation=op)
    if op == "create_additional_token":
        return _additional_token_from_dict(value, operation=op)
    if op == "multiply_token_creation":
        return _token_multiplier_from_dict(value, operation=op)
    if op == "reserve_zone_change":
        if "objects" in value:
            _exact_fields(value, {"op", "objects"}, operation=op)
            objects = value["objects"]
            if not isinstance(objects, (list, tuple)):
                raise ReplacementOperationError(
                    "Zone-change reservation objects must be an array"
                )
            return ReserveZoneChange(objects=tuple(str(item) for item in objects))
        _exact_fields(value, {"op", "from_field"}, operation=op)
        return ReserveZoneChange(from_field=_field(value["from_field"], operation=op))
    if op == "cap_result_life_loss":
        _exact_fields(value, {"op", "minimum"}, operation=op)
        return CapResultLifeLoss(
            _integer(value["minimum"], field="result life floor minimum")
        )
    if op == "prevent_draw":
        _exact_fields(value, {"op"}, operation=op)
        return PreventDraw()
    if op == "create_result_draws":
        _exact_fields(value, {"op", "count"}, operation=op)
        return CreateResultDraws(
            _integer(value["count"], field="result draw count", minimum=1)
        )
    if op == "dredge_draw":
        return _dredge_draw_from_dict(value, operation=op)
    raise ReplacementOperationError(
        f"Unsupported replacement operation {op!r}"
    )


def lower_operation(value: Any) -> ReplacementOperation:
    if isinstance(value, _TYPED_OPERATION_TYPES):
        return value
    if not isinstance(value, Mapping):
        raise ReplacementOperationError(
            "Replacement operations must lower from objects"
        )
    return operation_from_dict(value)


def operation_to_dict(value: ReplacementOperation) -> dict[str, Any]:
    if not isinstance(value, _TYPED_OPERATION_TYPES):
        raise ReplacementOperationError("Unknown typed replacement operation")
    return value.to_dict()
