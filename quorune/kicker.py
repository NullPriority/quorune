from __future__ import annotations

"""Typed Kicker costs and kicked-entry result contracts."""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .compiler.oracle_source_text import without_parenthetical_reminder
from .replacement.immutable import FrozenMap, thaw_value
from .util import mana_cost_to_vector, stable_json
from .zone_object_keyword_model import normalized_zone_object_keyword


KICKER_CAPABILITY_ID = "casting.kicker.fixed_mana"
POSITIVE_GENERIC_KICKER_CAPABILITY = "casting.kicker.positive_generic_x"
POSITIVE_GENERIC_KICKER_X = "positive_generic_kicker_x"
KICKED_ENTRY_CAPABILITY_ID = "replacement.kicker.fixed_entry"
KICKER_COST_HANDLER_ID = "casting.kicker.fixed-mana.v1"
KICKED_ENTRY_HANDLER_ID = "replacement.zone.kicked-entry.v1"
KICKER_RUNTIME_EVENT = "cast.cost"
KICKED_ENTRY_EVENT = "zone.change"
KICKER_MECHANIC_ID = "kicker"
KICKER_CAST_OPTION_ID = "kicked"
KICKER_ANNOTATION = "kicker_paid"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_ORDINARY_COST = r"(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+"
_ORDINARY_COST_PATTERN = re.compile(_ORDINARY_COST)
_KICKER_LINE = re.compile(
    rf"^Kicker\s+(?P<cost>{_ORDINARY_COST})\.?$",
    re.IGNORECASE,
)
_POSITIVE_X_KICKER_LINE = re.compile(r"^Kicker \{X\}\. X can['’]t be 0\.$", re.IGNORECASE)
_KICKED_ENTRY_LINE = re.compile(
    r"^If this (?:creature|permanent) was kicked, it enters with "
    r"(?P<amount>a|one|two|three|four|five|[1-9]\d*) "
    r"\+1/\+1 counters? on it"
    r"(?: and with (?P<keyword>flying|first strike|haste|trample))?\.$",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "a": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")


class KickerError(ValueError):
    """A Kicker descriptor, cast marker, or result is malformed."""


def _exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    *,
    field: str,
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        details = [
            *(f"missing {name}" for name in missing),
            *(f"unknown {name}" for name in unknown),
        ]
        raise KickerError(f"{field}: {'; '.join(details)}")


@dataclass(frozen=True, slots=True)
class FixedManaKickerSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise KickerError("Unsupported mana Kicker schema version")
        if _ABILITY_ID.fullmatch(self.ability_id) is None:
            raise KickerError("Kicker ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise KickerError("Kicker line index must be nonnegative")
        if type(self.oracle_line) is not str or not self.oracle_line:
            raise KickerError("Kicker Oracle line is required")
        if self.has_positive_generic_x:
            if self.cost_text != "{X}" or not _POSITIVE_X_KICKER_LINE.fullmatch(
                without_parenthetical_reminder(self.oracle_line)
            ):
                raise KickerError("Variable Kicker requires the exact positive generic X clause")
        elif re.fullmatch(_ORDINARY_COST, self.cost_text) is None:
            raise KickerError("Kicker cost must use fixed ordinary mana")
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise KickerError("Kicker mana cost must be an object")
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        expected, complex_symbols = mana_cost_to_vector("{0}" if self.has_positive_generic_x else self.cost_text)
        if (
            set(mana) != set(_MANA_FIELDS)
            or any(type(value) is not int or value < 0 for value in mana.values())
            or complex_symbols
            or mana != expected
        ):
            raise KickerError("Kicker mana vector does not match its cost")

    @property
    def has_positive_generic_x(self) -> bool:
        return self.schema_version == 2

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(stable_json(self.to_dict()).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedManaKickerSpec":
        _exact_fields(
            value,
            {
                "schema_version",
                "ability_id",
                "line_index",
                "oracle_line",
                "cost_text",
                "mana_cost",
            },
            field="fixed-mana Kicker descriptor",
        )
        mana = value["mana_cost"]
        if not isinstance(mana, Mapping):
            raise KickerError("Kicker mana cost must be an object")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(mana),
            schema_version=value["schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_cost": thaw_value(self.mana_cost),
        }

    def cast_cost_option(self) -> dict[str, Any]:
        return {
            "id": KICKER_CAST_OPTION_ID,
            "kind": KICKER_MECHANIC_ID,
            "label": f"Kicker {self.cost_text}",
            "requirements": thaw_value(self.mana_cost),
            "kicker_fingerprint": self.fingerprint,
            **({POSITIVE_GENERIC_KICKER_X: True} if self.has_positive_generic_x else {}),
        }


@dataclass(frozen=True, slots=True)
class FixedKickedEntrySpec:
    counter_amount: int
    keyword: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise KickerError("Unsupported kicked-entry schema version")
        if type(self.counter_amount) is not int or self.counter_amount < 1:
            raise KickerError("Kicked entry requires positive +1/+1 counters")
        if self.keyword is not None:
            try:
                keyword = normalized_zone_object_keyword(self.keyword)
            except ValueError as exc:
                raise KickerError(str(exc)) from exc
            if keyword not in {"flying", "first strike", "haste", "trample"}:
                raise KickerError("Kicked entry keyword is outside the closed family")
            object.__setattr__(self, "keyword", keyword)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedKickedEntrySpec":
        _exact_fields(
            value,
            {"schema_version", "counter_amount", "keyword"},
            field="fixed kicked-entry descriptor",
        )
        return cls(
            counter_amount=value["counter_amount"],
            keyword=value["keyword"],
            schema_version=value["schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "counter_amount": self.counter_amount,
            "keyword": self.keyword,
        }


def compile_fixed_mana_kicker(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> FixedManaKickerSpec | None:
    match = _KICKER_LINE.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    mana, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return FixedManaKickerSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_cost=FrozenMap(mana),
    )


def supports_positive_generic_kicker(record: Any, face_id: str) -> bool:
    return (
        record is not None
        and face_id == "front"
        and record.layout == "normal"
        and not record.faces
        and bool(_ORDINARY_COST_PATTERN.fullmatch(record.mana_cost or ""))
    )


def compile_positive_generic_kicker(
    *, material_line: str, oracle_line: str, line_index: int,
) -> FixedManaKickerSpec | None:
    if not _POSITIVE_X_KICKER_LINE.fullmatch(material_line.strip()):
        return None
    mana, _ = mana_cost_to_vector("{0}")
    return FixedManaKickerSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text="{X}",
        mana_cost=FrozenMap(mana),
        schema_version=2,
    )


def positive_generic_kicker_runtime_dependencies(program):
    for descriptor in program.handlers:
        if (descriptor.get("handler_id") == KICKER_COST_HANDLER_ID
                and isinstance(descriptor.get("kicker"), Mapping)
                and descriptor["kicker"].get("schema_version") == 2):
            from .semantic_runtime.kicker import FixedManaKickerHandler

            FixedManaKickerHandler().validate(descriptor)
            if (program.event, program.active_zone) != (KICKER_RUNTIME_EVENT, "all"):
                raise KickerError("Variable Kicker requires the original cast cost scope")
            return (POSITIVE_GENERIC_KICKER_CAPABILITY,)
    return ()


def compile_fixed_kicked_entry(material_line: str) -> FixedKickedEntrySpec | None:
    match = _KICKED_ENTRY_LINE.fullmatch(material_line.strip())
    if match is None:
        return None
    amount_text = match.group("amount").casefold()
    amount = _NUMBER_WORDS.get(amount_text, int(amount_text) if amount_text.isdigit() else 0)
    keyword = match.group("keyword")
    return FixedKickedEntrySpec(counter_amount=amount, keyword=keyword)


def kicker_cost_handler_descriptor(spec: FixedManaKickerSpec) -> dict[str, Any]:
    return {
        "handler_id": KICKER_COST_HANDLER_ID,
        "schema_version": 1,
        "event": KICKER_RUNTIME_EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "kicker": spec.to_dict(),
    }


def kicked_entry_handler_descriptor(spec: FixedKickedEntrySpec) -> dict[str, Any]:
    return {
        "handler_id": KICKED_ENTRY_HANDLER_ID,
        "schema_version": 1,
        "event": KICKED_ENTRY_EVENT,
        "entry": spec.to_dict(),
    }


__all__ = [
    "compile_fixed_kicked_entry",
    "compile_fixed_mana_kicker",
    "FixedKickedEntrySpec",
    "FixedManaKickerSpec",
    "kicked_entry_handler_descriptor",
    "kicker_cost_handler_descriptor",
    "KICKED_ENTRY_CAPABILITY_ID",
    "KICKED_ENTRY_EVENT",
    "KICKED_ENTRY_HANDLER_ID",
    "KICKER_ANNOTATION",
    "KICKER_CAPABILITY_ID",
    "KICKER_CAST_OPTION_ID",
    "KICKER_COST_HANDLER_ID",
    "KICKER_MECHANIC_ID",
    "KICKER_RUNTIME_EVENT",
    "KickerError",
]
