from __future__ import annotations

"""Closed fixed mana-cost options for compiler-pinned activations."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .replacement.immutable import FrozenMap, thaw_value
from .mana_restrictions import ability_mana_spend_context


_MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_COLORS = frozenset("WUBRG")
_MAX_OPTIONS = 128


def activation_mana_option_runtime_dependencies(program: Any) -> tuple[str, ...]:
    """Keep typed activation price alternatives bound to their existing owner."""
    schema = getattr(program, "cost_schema", None)
    if not isinstance(schema, Mapping) or not schema.get("mana_cost_options"):
        return ()
    rows = schema["mana_cost_options"]
    if not isinstance(rows, (list, tuple)) or getattr(program, "event", None) != "activate":
        raise ValueError("Activation mana options require their complete activation cost schema")
    options = tuple(ActivationManaCostOption.from_dict(row) for row in rows)
    if len({option.option_id for option in options}) != len(options):
        raise ValueError("Activation mana option identities must be unique")
    return ("activation.mana_cost.fixed_complex",)


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
    source: Any | None = None,
    *, modifiers=(),
) -> tuple[ActivationManaCostOption, ...]:
    if modifiers:
        return _modified_activation_mana_options(host, seat, source, ability, modifiers)
    reduction = int(
        getattr(ability, "generic_reduction_per_legendary_creature", 0)
    ) * max(0, host._legendary_creatures_controlled(seat))
    options = reduced_activation_mana_options(
        tuple(getattr(ability, "mana_cost_options", ())),
        generic_reduction=reduction,
    )
    if getattr(ability,'power_up',None) is not None:
        from .power_up import current_power_up_mana_options
        return current_power_up_mana_options(host,source,ability,options)
    return options


def _modified_activation_mana_options(host, seat, source, ability, modifiers):
    import hashlib
    from .activation_cost_modifiers import ActivationManaReduction, activation_cost_prices
    from .util import stable_json

    legendary = int(ability.generic_reduction_per_legendary_creature) * max(
        0, host._legendary_creatures_controlled(seat))
    power_up = ()
    if ability.power_up is not None:
        from .power_up import current_power_up_reductions
        power_up = current_power_up_reductions(host, source, ability)
    variants = {}
    for option in ability.mana_cost_options:
        for power_reduction in power_up or (FrozenMap(),):
            reductions = (ActivationManaReduction(FrozenMap({'GENERIC': legendary})),
                ActivationManaReduction(power_reduction))
            for price in activation_cost_prices(option.requirements, modifiers,
                    own_reductions=reductions, snow_payment=option.snow_payment):
                identity = {'requirements': dict(price.requirements),
                    'life': option.life_payment, 'snow': option.snow_payment}
                digest = hashlib.sha256(stable_json(identity).encode('utf-8')).hexdigest()[:16]
                variants[digest] = ActivationManaCostOption('modified-'+digest, price.requirements,
                    life_payment=option.life_payment, snow_payment=option.snow_payment)
                if len(variants) > _MAX_OPTIONS:
                    raise ValueError('Modified activation mana choices exceed the closed bound')
    return tuple(variants[key] for key in sorted(variants))


def payable_activation_mana_options(
    host: ActivationManaCostHost,
    seat: str,
    source: Any,
    ability: Any,
    *, modifiers=(),
) -> tuple[ActivationManaCostOption, ...]:
    base_life = int(getattr(ability, "life_payment", 0))
    excluded = (
        {source.object_id}
        if bool(getattr(ability, "tap_source", False))
        else set()
    )
    spend_context = ability_mana_spend_context(
        str(host._effective_card_data(source).get("type_line") or "")
    )
    return tuple(
        option
        for option in current_activation_mana_options(
            host,
            seat,
            ability,
            source,
            modifiers=modifiers,
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
    *, modifiers=(),
) -> ActivationManaCostOption:
    payable = payable_activation_mana_options(host, seat, source, ability, modifiers=modifiers)
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
