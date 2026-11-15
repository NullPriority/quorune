from __future__ import annotations

"""Typed fixed-mana Evoke cost and shared sacrifice-on-entry marker."""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from .replacement.immutable import FrozenMap, thaw_value
from .util import parse_mana_symbols, stable_json


EVOKE_CAPABILITY_ID = "casting.evoke.fixed_mana"
EVOKE_HANDLER_ID = "casting.evoke.fixed-mana.v1"
EVOKE_MECHANIC_ID = "evoke"
EVOKE_RUNTIME_EVENT = "cast.cost"
EVOKE_PAYMENT_FIELD = "evoke_payment"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_FIXED_SYMBOL = r"(?:0|[1-9]\d*|[WUBRGC]|[WUBRGC]/[WUBRGC])"
_FIXED_COST = rf"(?:\{{{_FIXED_SYMBOL}\}})+"
_EVOKE = re.compile(
    rf"^Evoke (?P<cost>{_FIXED_COST})(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")


class EvokeError(ValueError):
    """A fixed-mana Evoke descriptor or marker is malformed."""


def _empty_vector() -> dict[str, int]:
    return {field: 0 for field in _MANA_FIELDS}


def fixed_mana_variants(cost_text: str) -> tuple[FrozenMap, ...]:
    from .fixed_mana_model import FixedManaCostError,fixed_mana_variants as shared_variants
    try:return shared_variants(cost_text)
    except FixedManaCostError as exc:raise EvokeError(str(exc).replace('Fixed mana','Evoke')) from exc


@dataclass(frozen=True, slots=True)
class FixedManaEvokeSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_variants: tuple[FrozenMap, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise EvokeError("Unsupported fixed-mana Evoke schema version")
        if type(self.ability_id) is not str or _ABILITY_ID.fullmatch(self.ability_id) is None:
            raise EvokeError("Evoke ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise EvokeError("Evoke line index must be nonnegative")
        if self.ability_id != f"ab{self.line_index + 1}":
            raise EvokeError("Evoke ability ID does not match its source line")
        if type(self.oracle_line) is not str or _EVOKE.fullmatch(self.oracle_line.strip()) is None:
            raise EvokeError("Evoke Oracle line is outside the closed grammar")
        match = _EVOKE.fullmatch(self.oracle_line.strip())
        assert match is not None
        if type(self.cost_text) is not str or match.group("cost").upper() != self.cost_text:
            raise EvokeError("Evoke cost does not match its Oracle line")
        expected = fixed_mana_variants(self.cost_text)
        variants = tuple(
            value if isinstance(value, FrozenMap) else FrozenMap(value)
            for value in self.mana_variants
        )
        object.__setattr__(self, "mana_variants", variants)
        if variants != expected:
            raise EvokeError("Evoke mana variants do not match its cost")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(stable_json(self.to_dict()).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_variants": [thaw_value(value) for value in self.mana_variants],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedManaEvokeSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "mana_variants",
        }:
            raise EvokeError("Fixed-mana Evoke descriptors have a closed schema")
        variants = value["mana_variants"]
        if not isinstance(variants, list) or any(
            not isinstance(item, Mapping) for item in variants
        ):
            raise EvokeError("Evoke mana variants must be an array of objects")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_variants=tuple(FrozenMap(item) for item in variants),
            schema_version=value["schema_version"],
        )

    def cast_cost_options(self) -> tuple[dict[str, Any], ...]:
        multiple = len(self.mana_variants) > 1
        return tuple(
            {
                "id": f"evoke-hybrid-{index}" if multiple else "evoke",
                "kind": "alternate",
                "label": f"Evoke {self.cost_text}",
                "requirements": thaw_value(requirements),
                "evoke_fingerprint": self.fingerprint,
                EVOKE_PAYMENT_FIELD: evoke_payment_marker(schema_version=self.schema_version),
            }
            for index, requirements in enumerate(self.mana_variants, start=1)
        )


def compile_fixed_mana_evoke(
    *, material_line: str, oracle_line: str, line_index: int,
) -> FixedManaEvokeSpec | None:
    match = _EVOKE.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    try:
        variants = fixed_mana_variants(cost_text)
    except EvokeError:
        return None
    return FixedManaEvokeSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_variants=variants,
        schema_version=2,
    )


def evoke_payment_marker(*, schema_version: int = 1) -> dict[str, Any]:
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise EvokeError("Unsupported Evoke payment marker version")
    return {
        "schema_version": schema_version,
        "kind": EVOKE_MECHANIC_ID,
        "sacrifice_on_entry": True,
    }


def validate_evoke_payment_marker(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"schema_version", "kind", "sacrifice_on_entry"}
        and type(value["schema_version"]) is int
        and value["schema_version"] in {1, 2}
        and value["kind"] == EVOKE_MECHANIC_ID
        and value["sacrifice_on_entry"] is True
    )


def evoke_payment_annotation(marker: Mapping[str, Any]) -> bool | dict[str, Any]:
    if not validate_evoke_payment_marker(marker):
        raise EvokeError("Malformed Evoke payment marker")
    return True if marker["schema_version"] == 1 else dict(marker)


def is_evoked_annotation(value: Any) -> bool:
    return value is True or (validate_evoke_payment_marker(value) and value["schema_version"] == 2)


def evoke_sacrifice_source_reference(context: Mapping[str, Any]) -> str:
    if "evoke_source_identity_version" not in context:
        return "$source"
    if (type(context["evoke_source_identity_version"]) is not int
            or context["evoke_source_identity_version"] != 2 or context.get("evoke") is not True
            or type(context.get("source_logical_object_id")) is not str
            or not context["source_logical_object_id"]):
        from .errors import GameRuleError
        raise GameRuleError("The Evoke source identity context is malformed")
    return "$source.zone_object"


def evoke_sacrifice_effect(context: Mapping[str, Any], *, source: Any,
                           controller: str) -> dict[str, Any] | None:
    reference = evoke_sacrifice_source_reference(context)
    if reference == "$source.zone_object" and (
        source is None or source.zone != "battlefield" or source.phased_out
        or source.logical_object_id != context["source_logical_object_id"]
        or source.controller != controller
    ):
        return None
    return {"op": "sacrifice_if_present", "card": reference}


def evoke_sacrifice_effects(item: Any, cards: Mapping[str, Any]) -> list[dict[str, Any]]:
    effect = evoke_sacrifice_effect(item.context, source=cards.get(item.source_object_id),
                                   controller=item.controller)
    return [effect] if effect is not None else []


__all__ = [
    "EVOKE_CAPABILITY_ID",
    "EVOKE_HANDLER_ID",
    "EVOKE_MECHANIC_ID",
    "EVOKE_PAYMENT_FIELD",
    "EVOKE_RUNTIME_EVENT",
    "EvokeError",
    "FixedManaEvokeSpec",
    "compile_fixed_mana_evoke",
    "evoke_payment_marker",
    "fixed_mana_variants",
    "validate_evoke_payment_marker",
    "evoke_payment_annotation",
    "is_evoked_annotation",
    "evoke_sacrifice_source_reference",
    "evoke_sacrifice_effect",
    "evoke_sacrifice_effects",
]
