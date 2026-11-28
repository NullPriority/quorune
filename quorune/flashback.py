from __future__ import annotations

"""Typed ordinary fixed Flashback casting and stack departure."""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .model import CardInstance
from .replacement.immutable import FrozenMap, thaw_value
from .replacement.model import ReplacementClass, ReplacementEffect
from .replacement.operations import SetField
from .rules.casting_additional_cost_groups import FixedLifePaymentAdditionalCost
from .util import mana_cost_to_vector, stable_json


FLASHBACK_CAPABILITY_ID = "casting.flashback.fixed_mana"
FLASHBACK_CAST_ANNOTATION = "cast_via_flashback"
FLASHBACK_CAST_OPTION_ID = "flashback"
FLASHBACK_HANDLER_ID = "casting.flashback.fixed-mana.v1"
FLASHBACK_MECHANIC_ID = "flashback"
FLASHBACK_RUNTIME_EVENT = "cast.cost"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_ORDINARY_COST = r"(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+"
_FIXED_FLASHBACK = re.compile(
    rf"^Flashback(?:\s+(?P<mana_only>{_ORDINARY_COST})|"
    rf"[—–]\s*(?P<mana_life>{_ORDINARY_COST}),\s*"
    r"Pay (?P<life>[1-9]\d*) life)"
    r"\.?(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")


class FlashbackError(ValueError):
    """A Flashback descriptor or designation is malformed."""


@dataclass(frozen=True, slots=True)
class FixedManaFlashbackSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    life_payment: int | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FlashbackError("Unsupported fixed-mana Flashback schema version")
        if (
            type(self.ability_id) is not str
            or _ABILITY_ID.fullmatch(self.ability_id) is None
        ):
            raise FlashbackError("Flashback ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise FlashbackError("Flashback line index must be nonnegative")
        if self.ability_id != f"ab{self.line_index + 1}":
            raise FlashbackError("Flashback ability ID does not match its source line")
        if type(self.oracle_line) is not str or not self.oracle_line:
            raise FlashbackError("Flashback Oracle line is required")
        oracle_match = _FIXED_FLASHBACK.fullmatch(self.oracle_line.strip())
        if oracle_match is None:
            raise FlashbackError("Flashback Oracle line is outside the closed grammar")
        if (
            type(self.cost_text) is not str
            or re.fullmatch(_ORDINARY_COST, self.cost_text) is None
        ):
            raise FlashbackError("Flashback cost must use fixed ordinary mana")
        matched_cost = oracle_match.group("mana_only") or oracle_match.group(
            "mana_life"
        )
        if matched_cost.upper() != self.cost_text:
            raise FlashbackError("Flashback cost does not match its Oracle line")
        matched_life = (
            int(oracle_match.group("life")) if oracle_match.group("life") else None
        )
        if self.life_payment != matched_life or (
            self.life_payment is not None
            and (type(self.life_payment) is not int or self.life_payment <= 0)
        ):
            raise FlashbackError("Flashback life payment does not match its Oracle line")
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise FlashbackError("Flashback mana cost must be an object")
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        if (
            set(mana) != set(_MANA_FIELDS)
            or any(type(value) is not int or value < 0 for value in mana.values())
            or complex_symbols
            or mana != expected
        ):
            raise FlashbackError("Flashback mana vector does not match its cost")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(stable_json(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedManaFlashbackSpec":
        expected = {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "mana_cost",
            "life_payment",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise FlashbackError("Fixed-mana Flashback descriptors have a closed schema")
        mana_cost = value["mana_cost"]
        if not isinstance(mana_cost, Mapping):
            raise FlashbackError("Flashback mana cost must be an object")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(mana_cost),
            life_payment=value["life_payment"],
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
            "life_payment": self.life_payment,
        }

    def cast_cost_option(self) -> dict[str, Any]:
        option = {
            "id": FLASHBACK_CAST_OPTION_ID,
            "kind": "alternate",
            "label": (
                f"Flashback {self.cost_text}"
                if self.life_payment is None
                else f"Flashback—{self.cost_text}, Pay {self.life_payment} life"
            ),
            "requirements": thaw_value(self.mana_cost),
            "flashback_fingerprint": self.fingerprint,
            "source_zone": "graveyard",
            "x_value_policy": "zero",
        }
        if self.life_payment is not None:
            option["_additional_option_costs"] = [
                FixedLifePaymentAdditionalCost(
                    self.life_payment
                ).to_descriptor()
            ]
        return option


def compile_fixed_mana_flashback(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> FixedManaFlashbackSpec | None:
    match = _FIXED_FLASHBACK.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = (match.group("mana_only") or match.group("mana_life")).upper()
    mana, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return FixedManaFlashbackSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_cost=FrozenMap(mana),
        life_payment=(int(match.group("life")) if match.group("life") else None),
    )


def flashback_handler_descriptor(spec: FixedManaFlashbackSpec) -> dict[str, Any]:
    return {
        "handler_id": FLASHBACK_HANDLER_ID,
        "schema_version": 1,
        "event": FLASHBACK_RUNTIME_EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "flashback": spec.to_dict(),
    }


def flashed_back_leave_replacement(card: CardInstance) -> ReplacementEffect | None:
    """Return Flashback's mandatory replacement for the current stack object."""
    return paid_stack_exile_replacement(
        card, annotation=FLASHBACK_CAST_ANNOTATION, rule="flashback",
        description="exile the flashed-back spell instead",
    )


def paid_stack_exile_replacement(card, *, annotation, rule, description):

    if (
        not isinstance(card, CardInstance)
        or card.zone != "stack"
        or card.object_kind != "card"
        or card.annotations.get(annotation) is not True
    ):
        return None
    return ReplacementEffect(
        effect_id=f"rule:{rule}:{card.logical_object_id}",
        source_id=card.ref,
        event_kind="zone.change",
        replacement_class=ReplacementClass.SELF_REPLACEMENT,
        conditions={
            "origin": {"eq": "stack"},
            "destination": {"not_in": ["exile"]},
            "object_ref": {"eq": card.ref},
            "logical_object_id": {"eq": card.logical_object_id},
        },
        operations=(SetField("destination", "exile"),),
        label=f"{card.ref}: {description}",
    )


def flashed_back_subject_replacements(
    cards: Mapping[str, CardInstance],
    object_ids: Iterable[str],
) -> tuple[ReplacementEffect, ...]:
    """Collect current Flashback self-replacements for zone-move subjects."""

    return tuple(
        replacement
        for object_id in object_ids
        if (replacement := flashed_back_leave_replacement(cards.get(object_id)))
        is not None
    )


__all__ = [
    "compile_fixed_mana_flashback",
    "FixedManaFlashbackSpec",
    "flashed_back_leave_replacement",
    "flashed_back_subject_replacements",
    "flashback_handler_descriptor",
    "FLASHBACK_CAPABILITY_ID",
    "FLASHBACK_CAST_ANNOTATION",
    "FLASHBACK_CAST_OPTION_ID",
    "FLASHBACK_HANDLER_ID",
    "FLASHBACK_MECHANIC_ID",
    "FLASHBACK_RUNTIME_EVENT",
    "FlashbackError",
]
