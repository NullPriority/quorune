from __future__ import annotations

"""Closed fixed mana-cost options for compiler-pinned activations."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .replacement.immutable import FrozenMap, thaw_value


_MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_COLORS = frozenset("WUBRG")
_MAX_OPTIONS = 128


def _requirements(value: Mapping[str, int]) -> dict[str, int]:
    unknown = set(value).difference(_MANA_KEYS)
    if unknown:
        raise ValueError("activation mana-cost option has unsupported mana")
    if any(type(amount) is not int or amount < 0 for amount in value.values()):
        raise ValueError("activation mana-cost requirements are invalid")
    return {key: value.get(key, 0) for key in _MANA_KEYS}


@dataclass(frozen=True, slots=True)
class ActivationManaCostOption:
    option_id: str
    requirements: FrozenMap
    life_payment: int = 0
    snow_payment: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or type(self.option_id) is not str
            or not self.option_id.strip()
        ):
            raise ValueError("activation mana-cost option identity is invalid")
        requirements = _requirements(self.requirements)
        object.__setattr__(self, "requirements", FrozenMap(requirements))
        if type(self.life_payment) is not int or self.life_payment < 0:
            raise ValueError("activation mana-cost life payment is invalid")
        if type(self.snow_payment) is not int or self.snow_payment < 0:
            raise ValueError("activation snow-mana payment is invalid")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActivationManaCostOption":
        expected = {
            "schema_version",
            "id",
            "requirements",
            "life_payment",
            "snow_payment",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("activation mana-cost options use a closed schema")
        if not isinstance(value["requirements"], Mapping):
            raise ValueError("activation mana-cost requirements must be an object")
        return cls(
            option_id=value["id"],
            requirements=FrozenMap(value["requirements"]),
            life_payment=value["life_payment"],
            snow_payment=value["snow_payment"],
            schema_version=value["schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.option_id,
            "requirements": thaw_value(self.requirements),
            "life_payment": self.life_payment,
            "snow_payment": self.snow_payment,
        }

    def compact(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.option_id,
            "m": {
                key: value
                for key, value in self.requirements.items()
                if value
            },
        }
        if self.life_payment:
            result["life"] = self.life_payment
        if self.snow_payment:
            result["snow"] = self.snow_payment
        return result


def _option_key(
    value: tuple[dict[str, int], int, int],
) -> tuple[int, int, tuple[int, ...]]:
    mana, life, snow = value
    return life, snow, tuple(mana[key] for key in _MANA_KEYS)


def fixed_complex_activation_mana_options(
    base_mana: Mapping[str, int],
    complex_symbols: Sequence[str],
) -> tuple[ActivationManaCostOption, ...]:
    """Expand one closed fixed complex cost without consulting game state."""

    if not complex_symbols:
        return ()
    variants: list[tuple[dict[str, int], int, int]] = [
        (_requirements(base_mana), 0, 0)
    ]
    for raw_symbol in complex_symbols:
        symbol = str(raw_symbol).upper()
        choices: list[tuple[str, str | int]] = []
        parts = symbol.split("/")
        if (
            len(parts) == 2
            and len(set(parts)) == 2
            and set(parts).issubset(_COLORS)
        ):
            choices = [("mana", part) for part in parts]
        elif (
            len(parts) == 2
            and "2" in parts
            and len(set(parts).intersection(_COLORS)) == 1
        ):
            color = next(part for part in parts if part != "2")
            choices = (("generic", 2), ("mana", color))
        elif len(parts) == 2 and parts[1] == "P" and parts[0] in _COLORS:
            choices = (("mana", parts[0]), ("life", 2))
        elif symbol == "S":
            choices = (("snow", 1),)
        else:
            return ()
        expanded: list[tuple[dict[str, int], int, int]] = []
        for mana, life, snow in variants:
            for kind, raw_value in choices:
                next_mana = dict(mana)
                next_life = life
                next_snow = snow
                if kind == "mana":
                    next_mana[str(raw_value)] += 1
                elif kind == "generic":
                    next_mana["GENERIC"] += int(raw_value)
                elif kind == "life":
                    next_life += int(raw_value)
                else:
                    next_snow += int(raw_value)
                expanded.append((next_mana, next_life, next_snow))
        deduplicated = {
            _option_key(value): value for value in expanded
        }
        variants = [deduplicated[key] for key in sorted(deduplicated)]
        if len(variants) > _MAX_OPTIONS:
            return ()
    multiple = len(variants) > 1
    return tuple(
        ActivationManaCostOption(
            option_id=(f"complex-{index}" if multiple else "complex"),
            requirements=FrozenMap(mana),
            life_payment=life,
            snow_payment=snow,
        )
        for index, (mana, life, snow) in enumerate(variants, start=1)
    )


def reduced_activation_mana_options(
    options: Sequence[ActivationManaCostOption],
    *,
    generic_reduction: int = 0,
) -> tuple[ActivationManaCostOption, ...]:
    reduction = max(0, int(generic_reduction))
    result = []
    for option in options:
        requirements = dict(option.requirements)
        requirements["GENERIC"] = max(
            0, requirements["GENERIC"] - reduction
        )
        result.append(
            ActivationManaCostOption(
                option.option_id,
                FrozenMap(requirements),
                life_payment=option.life_payment,
                snow_payment=option.snow_payment,
            )
        )
    return tuple(result)


class ActivationManaCostHost(Protocol):
    state: Any

    def _legendary_creatures_controlled(self, seat: str) -> int: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
        *,
        exclude_sources: set[str] | None = None,
        spend_context: str | None = None,
        snow_required: int = 0,
    ) -> bool: ...


def current_activation_mana_options(
    host: ActivationManaCostHost,
    seat: str,
    ability: Any,
) -> tuple[ActivationManaCostOption, ...]:
    reduction = int(
        getattr(ability, "generic_reduction_per_legendary_creature", 0)
    ) * max(0, host._legendary_creatures_controlled(seat))
    return reduced_activation_mana_options(
        tuple(getattr(ability, "mana_cost_options", ())),
        generic_reduction=reduction,
    )


def payable_activation_mana_options(
    host: ActivationManaCostHost,
    seat: str,
    source: Any,
    ability: Any,
) -> tuple[ActivationManaCostOption, ...]:
    base_life = int(getattr(ability, "life_payment", 0))
    excluded = (
        {source.object_id}
        if bool(getattr(ability, "tap_source", False))
        else set()
    )
    source_types = host._type_parts(
        str(host._effective_card_data(source).get("type_line") or "")
    )[0]
    spend_context = (
        "artifact_ability" if "artifact" in source_types else "ability"
    )
    return tuple(
        option
        for option in current_activation_mana_options(
            host,
            seat,
            ability,
        )
        if host.state.players[seat].life
        >= base_life + option.life_payment
        and host._cost_is_affordable(
            seat,
            option.requirements,
            exclude_sources=excluded,
            spend_context=spend_context,
            snow_required=option.snow_payment,
        )
    )


def select_activation_mana_option(
    host: ActivationManaCostHost,
    seat: str,
    source: Any,
    ability: Any,
    requested_id: object,
) -> ActivationManaCostOption:
    payable = payable_activation_mana_options(host, seat, source, ability)
    requested = str(requested_id or "")
    if not requested:
        if len(payable) == 1:
            return payable[0]
        raise ValueError("Select one currently payable activation mana cost")
    option = next(
        (value for value in payable if value.option_id == requested),
        None,
    )
    if option is None:
        raise ValueError("Selected activation mana cost is not payable")
    return option


__all__ = [
    "ActivationManaCostOption",
    "fixed_complex_activation_mana_options",
    "current_activation_mana_options",
    "payable_activation_mana_options",
    "reduced_activation_mana_options",
    "select_activation_mana_option",
]
