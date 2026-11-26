from __future__ import annotations

"""Closed Echo payment descriptors and the shared control-history query."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .fixed_mana_abilities import MANA_COST_KEYS
from .replacement.immutable import FrozenMap, thaw_value
from .util import mana_cost_to_vector


ECHO_MECHANIC_ID = "echo"
ECHO_CONTROL_CONDITION_FIELD = "source_echo_control_condition"
DISCARD_ECHO_CAPABILITY = "trigger.keyword.echo.discard_one"
DISCARD_ECHO_VERSION = 2
DISCARD_ECHO_TEMPLATE = "fixed-discard-echo-v1"
_ORDINARY_COST = r"(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+"
_ORDINARY_ECHO = re.compile(
    rf"^Echo\s+(?P<cost>{_ORDINARY_COST})\.?$",
    re.IGNORECASE,
)
_DISCARD_ECHO = re.compile(r"Echo\s*[—–-]\s*discard a card\.?", re.IGNORECASE)


class EchoError(ValueError):
    """An ordinary fixed-mana Echo descriptor or history fact is malformed."""


def _require_exact_fields(
    value: Mapping[str, Any], expected: set[str], *, label: str
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise EchoError(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise EchoError(f"{label} has unknown fields: {', '.join(unknown)}")


@dataclass(frozen=True, slots=True)
class FixedManaEchoSpec:
    cost_text: str
    mana_cost: FrozenMap

    def __post_init__(self) -> None:
        if (
            not isinstance(self.cost_text, str)
            or re.fullmatch(_ORDINARY_COST, self.cost_text, re.IGNORECASE)
            is None
        ):
            raise EchoError(
                "Echo cost must contain only fixed ordinary mana symbols"
            )
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise EchoError("Echo mana cost must be an object")
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        if set(mana) != set(MANA_COST_KEYS) or any(
            type(amount) is not int or amount < 0 for amount in mana.values()
        ):
            raise EchoError(
                "Echo mana cost must use canonical nonnegative keys"
            )
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        if complex_symbols or mana != expected:
            raise EchoError("Echo mana cost does not match the printed cost")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_text": self.cost_text,
            "mana_cost": thaw_value(self.mana_cost),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedManaEchoSpec":
        _require_exact_fields(
            value,
            {"cost_text", "mana_cost"},
            label="fixed-mana Echo",
        )
        cost_text = value["cost_text"]
        mana_cost = value["mana_cost"]
        if not isinstance(cost_text, str) or not isinstance(mana_cost, Mapping):
            raise EchoError("Echo descriptor fields have invalid types")
        return cls(cost_text=cost_text, mana_cost=FrozenMap(mana_cost))

    def effect_descriptor(self) -> dict[str, Any]:
        return {
            "op": "echo_upkeep",
            "player": "$controller",
            "source": "$source",
            "cost": thaw_value(self.mana_cost),
        }


def compile_fixed_mana_echo(material_line: str) -> FixedManaEchoSpec | None:
    """Compile exactly one ordinary fixed-mana Echo keyword instance."""

    match = _ORDINARY_ECHO.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    mana_cost, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return FixedManaEchoSpec(
        cost_text=cost_text,
        mana_cost=FrozenMap(mana_cost),
    )


def echo_upkeep_condition() -> dict[str, Any]:
    return {"all": [
        {"field": "step", "op": "eq", "value": "upkeep"},
        {"field": ECHO_CONTROL_CONDITION_FIELD, "op": "truthy"},
    ]}


def discard_echo_payment(player: str):
    from .fixed_effect_payment import FixedEffectPaymentSpec
    from .object_predicate import ObjectQuerySpec

    return FixedEffectPaymentSpec(
        "discard", predicate=ObjectQuerySpec(zones=("hand",), owner=player, known_to_actor=True),
    )


def discard_echo_effect(player: str = "$controller", source: str = "$source") -> dict[str, Any]:
    return {
        "op": "echo_upkeep",
        "schema_version": DISCARD_ECHO_VERSION,
        "player": player,
        "source": source,
        "payment": discard_echo_payment(player).to_dict(),
    }


def compile_discard_echo(material_line: str) -> dict[str, Any] | None:
    if _DISCARD_ECHO.fullmatch(material_line.strip()) is None:
        return None
    return discard_echo_effect()


def validate_discard_echo_effect(effect: Mapping[str, Any], *, player: str, source: str):
    from .fixed_effect_payment import FixedEffectPaymentSpec

    expected = discard_echo_effect(player, source)
    if type(effect.get("schema_version")) is not int or thaw_value(FrozenMap(effect)) != expected:
        raise EchoError("Discard Echo requires the controller's exact one-card payment and source")
    return FixedEffectPaymentSpec.from_dict(effect["payment"])


def echo_control_condition_holds(
    source: Any,
    context: Mapping[str, Any],
) -> bool:
    """Evaluate Echo's source-identity-pinned intervening condition."""

    previous = context.get("previous_upkeep_timestamp")
    event_player = context.get("player")
    expected_logical = context.get("source_logical_object_id")
    if type(previous) is not int or previous < 0:
        raise EchoError(
            "Echo requires a nonnegative previous-upkeep timestamp"
        )
    if not isinstance(event_player, str) or not event_player:
        raise EchoError("Echo requires a nonempty upkeep player")
    if expected_logical is not None and (
        not isinstance(expected_logical, str) or not expected_logical
    ):
        raise EchoError("Echo source logical identity is malformed")
    if expected_logical is not None and source.logical_object_id != expected_logical:
        return False

    snapshot_controller = context.get("echo_control_acquisition_controller")
    snapshot_timestamp = context.get("echo_control_acquisition_timestamp")
    if snapshot_controller is None and snapshot_timestamp is None:
        snapshot_controller = source.controller
        snapshot_timestamp = source.acquired_control_timestamp
    if not isinstance(snapshot_controller, str) or not snapshot_controller:
        raise EchoError("Echo control-acquisition controller is malformed")
    if type(snapshot_timestamp) is not int or snapshot_timestamp < 0:
        raise EchoError("Echo control-acquisition timestamp is malformed")
    return (
        snapshot_controller == event_player
        and snapshot_timestamp > previous
    )


__all__ = [
    "ECHO_CONTROL_CONDITION_FIELD",
    "ECHO_MECHANIC_ID",
    "EchoError",
    "FixedManaEchoSpec",
    "compile_fixed_mana_echo",
    "echo_control_condition_holds",
]
