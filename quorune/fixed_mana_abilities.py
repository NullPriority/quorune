from __future__ import annotations

"""Typed fixed-output activated mana abilities.

This is intentionally a closed grammar.  It compiles target-free, nonloyalty
activated mana abilities whose complete output is known before activation.
Dynamic quantities, open restrictions, conditional output, and side effects
in the effect clause remain outside this family.
"""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .activation_usage import ActivationLimit
from .activation_condition_model import (
    ActivationCondition,
    ActivationConditionKind,
    activation_restriction_spec,
)
from .mana_restrictions import (
    legacy_mana_spend_restriction_for_tail,
    valid_mana_spend_restriction,
)
from .replacement.immutable import FrozenMap, thaw_value
from .util import normalize_mana_bundle


MANA_KEYS = ("W", "U", "B", "R", "G", "C")
MANA_COST_KEYS = ("GENERIC", *MANA_KEYS)
FIXED_MANA_HANDLER_ID = "ability.activated.mana.fixed-output.v1"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_SYMBOL_GROUP = re.compile(r"(?:\{[WUBRGC]\})+")
_ANY_COLOR = re.compile(
    r"^Add (?P<count>one|two|three) mana of any one color\.$",
    re.IGNORECASE,
)
_SPEND_RESTRICTION_MARKERS = (
    " Spend this mana only to ",
    " This mana can't be spent to ",
)


class FixedManaAbilityError(ValueError):
    """A fixed-output mana descriptor is malformed or unsupported."""


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, field: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise FixedManaAbilityError(
            f"{field} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise FixedManaAbilityError(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


@dataclass(frozen=True, slots=True)
class FixedManaMode:
    white: int = 0
    blue: int = 0
    black: int = 0
    red: int = 0
    green: int = 0
    colorless: int = 0

    def __post_init__(self) -> None:
        values = tuple(self.bundle.values())
        if any(type(value) is not int or value < 0 for value in values):
            raise FixedManaAbilityError(
                "Fixed mana output amounts must be nonnegative integers"
            )
        if not sum(values):
            raise FixedManaAbilityError(
                "A fixed mana output mode must add at least one mana"
            )

    @property
    def bundle(self) -> dict[str, int]:
        return {
            "W": self.white,
            "U": self.blue,
            "B": self.black,
            "R": self.red,
            "G": self.green,
            "C": self.colorless,
        }

    @classmethod
    def from_bundle(cls, value: Mapping[str, Any]) -> "FixedManaMode":
        unknown = sorted(
            repr(key) for key in value if key not in MANA_KEYS
        )
        if unknown:
            raise FixedManaAbilityError(
                "Fixed mana output has unknown symbols: "
                + ", ".join(unknown)
            )
        if any(
            type(amount) is not int or amount < 0
            for amount in value.values()
        ):
            raise FixedManaAbilityError(
                "Fixed mana output amounts must be nonnegative integers"
            )
        bundle = normalize_mana_bundle(value)
        return cls(
            white=bundle["W"],
            blue=bundle["U"],
            black=bundle["B"],
            red=bundle["R"],
            green=bundle["G"],
            colorless=bundle["C"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"bundle": self.bundle}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedManaMode":
        _exact_fields(value, {"bundle"}, field="fixed mana mode")
        bundle = value["bundle"]
        if not isinstance(bundle, Mapping) or set(bundle) != set(MANA_KEYS):
            raise FixedManaAbilityError(
                "Fixed mana mode bundle must contain exactly W, U, B, R, G, C"
            )
        return cls.from_bundle(bundle)


@dataclass(frozen=True, slots=True)
class FixedActivatedManaAbilitySpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    effect_text: str
    mana_cost: FrozenMap
    tap_source: bool
    sacrifice_source: bool
    life_payment: int
    modes: tuple[FixedManaMode, ...]
    spend_restriction: str | None = None
    activation_limit: ActivationLimit | None = None
    activation_conditions: tuple[ActivationCondition, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ability_id, str)
            or _ABILITY_ID.fullmatch(self.ability_id) is None
        ):
            raise FixedManaAbilityError("Fixed mana ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise FixedManaAbilityError(
                "Fixed mana ability line_index must be nonnegative"
            )
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.oracle_line,
                self.cost_text,
                self.effect_text,
            )
        ):
            raise FixedManaAbilityError(
                "Fixed mana ability text fields must be nonempty"
            )
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise FixedManaAbilityError(
                    "Fixed mana activation cost must be an object"
                )
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        if set(mana) != set(MANA_COST_KEYS) or any(
            type(value) is not int or value < 0 for value in mana.values()
        ):
            raise FixedManaAbilityError(
                "Fixed mana activation cost must contain canonical mana keys"
            )
        if type(self.tap_source) is not bool or type(self.sacrifice_source) is not bool:
            raise FixedManaAbilityError(
                "Fixed mana source-cost flags must be booleans"
            )
        if type(self.life_payment) is not int or self.life_payment < 0:
            raise FixedManaAbilityError(
                "Fixed mana life payment must be a nonnegative integer"
            )
        if (
            not isinstance(self.modes, tuple)
            or not self.modes
            or any(not isinstance(mode, FixedManaMode) for mode in self.modes)
        ):
            raise FixedManaAbilityError(
                "Fixed mana ability requires typed output modes"
            )
        if len(self.modes) != len({tuple(mode.bundle.items()) for mode in self.modes}):
            raise FixedManaAbilityError(
                "Fixed mana ability output modes must be unique"
            )
        if self.spend_restriction is not None and (
            not valid_mana_spend_restriction(self.spend_restriction)
        ):
            raise FixedManaAbilityError(
                "Fixed mana spending restriction is unsupported"
            )
        if self.activation_limit is not None and not isinstance(
            self.activation_limit, ActivationLimit
        ):
            try:
                object.__setattr__(
                    self,
                    "activation_limit",
                    ActivationLimit(self.activation_limit),
                )
            except (TypeError, ValueError) as exc:
                raise FixedManaAbilityError(
                    "Fixed mana activation limit is unsupported"
                ) from exc
        if not isinstance(self.activation_conditions, tuple) or any(
            not isinstance(condition, ActivationCondition)
            for condition in self.activation_conditions
        ):
            raise FixedManaAbilityError(
                "Fixed mana activation conditions must be typed predicates"
            )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "effect_text": self.effect_text,
            "mana_cost": thaw_value(self.mana_cost),
            "tap_source": self.tap_source,
            "sacrifice_source": self.sacrifice_source,
            "life_payment": self.life_payment,
            "modes": [mode.to_dict() for mode in self.modes],
        }
        if self.activation_limit is not None:
            value["activation_limit"] = self.activation_limit.value
        if self.activation_conditions:
            value["activation_conditions"] = [
                condition.to_dict() for condition in self.activation_conditions
            ]
        if self.spend_restriction is not None:
            value["spend_restriction"] = self.spend_restriction
        return value

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FixedActivatedManaAbilitySpec":
        expected = {
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "effect_text",
            "mana_cost",
            "tap_source",
            "sacrifice_source",
            "life_payment",
            "modes",
        }
        if "activation_limit" in value:
            expected.add("activation_limit")
        if "activation_conditions" in value:
            expected.add("activation_conditions")
        if "spend_restriction" in value:
            expected.add("spend_restriction")
        _exact_fields(value, expected, field="fixed mana ability")
        mana_cost = value["mana_cost"]
        modes = value["modes"]
        activation_conditions = value.get("activation_conditions", [])
        if not isinstance(mana_cost, Mapping):
            raise FixedManaAbilityError("Fixed mana activation cost must be an object")
        if not isinstance(modes, list) or any(
            not isinstance(mode, Mapping) for mode in modes
        ):
            raise FixedManaAbilityError("Fixed mana output modes must be an array")
        if not isinstance(activation_conditions, list) or any(
            not isinstance(condition, Mapping)
            for condition in activation_conditions
        ):
            raise FixedManaAbilityError(
                "Fixed mana activation conditions must be an array"
            )
        for field in ("ability_id", "oracle_line", "cost_text", "effect_text"):
            if not isinstance(value[field], str):
                raise FixedManaAbilityError(
                    f"Fixed mana ability {field} must be a string"
                )
        try:
            conditions = tuple(
                ActivationCondition.from_dict(condition)
                for condition in activation_conditions
            )
        except (TypeError, ValueError) as exc:
            raise FixedManaAbilityError(str(exc)) from exc
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            effect_text=value["effect_text"],
            mana_cost=FrozenMap(mana_cost),
            tap_source=value["tap_source"],
            sacrifice_source=value["sacrifice_source"],
            life_payment=value["life_payment"],
            modes=tuple(FixedManaMode.from_dict(mode) for mode in modes),
            spend_restriction=value.get("spend_restriction"),
            activation_limit=value.get("activation_limit"),
            activation_conditions=conditions,
        )

    def to_activated_ability(self) -> Any:
        from .abilities import ActivatedAbility

        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            effect_text=self.effect_text,
            zones=("battlefield",),
            mana=thaw_value(self.mana_cost),
            tap_source=self.tap_source,
            sacrifice_source=self.sacrifice_source,
            life_payment=self.life_payment,
            mana_ability=True,
            fixed_mana_outputs=self.modes,
            mana_spend_restriction=self.spend_restriction,
            activation_limit=self.activation_limit,
            activation_conditions=self.activation_conditions,
        )


def _symbol_bundle(text: str) -> FixedManaMode:
    bundle = {key: 0 for key in MANA_KEYS}
    for symbol in re.findall(r"\{([WUBRGC])\}", text.upper()):
        bundle[symbol] += 1
    return FixedManaMode.from_bundle(bundle)


def fixed_mana_modes_from_effect(
    effect_text: str,
) -> tuple[FixedManaMode, ...] | None:
    """Compile the complete fixed output clause or return ``None``."""

    text = " ".join(effect_text.split())
    if text.casefold() == "add one mana of any color.":
        return tuple(
            FixedManaMode.from_bundle({color: 1}) for color in "WUBRG"
        )
    if text.casefold() == "add one mana of any type.":
        return tuple(
            FixedManaMode.from_bundle({color: 1}) for color in "WUBRGC"
        )
    any_color = _ANY_COLOR.fullmatch(text)
    if any_color is not None:
        count = {"one": 1, "two": 2, "three": 3}[
            any_color.group("count").casefold()
        ]
        return tuple(
            FixedManaMode.from_bundle({color: count}) for color in "WUBRG"
        )
    symbols = re.fullmatch(r"Add (?P<body>.+)\.", text, re.IGNORECASE)
    if symbols is None:
        return None
    body = symbols.group("body")
    if "," in body or re.search(r"\s+or\s+", body, re.IGNORECASE):
        groups = tuple(
            part.strip()
            for part in re.split(r"\s*,\s*(?:or\s+)?|\s+or\s+", body)
            if part.strip()
        )
    else:
        groups = (body,)
    if not groups or any(_SYMBOL_GROUP.fullmatch(group) is None for group in groups):
        return None
    modes = tuple(_symbol_bundle(group) for group in groups)
    return modes if len(modes) == len({tuple(mode.bundle.items()) for mode in modes}) else None


def _restricted_modes_are_closed(
    restriction: str | None,
    modes: tuple[FixedManaMode, ...],
) -> bool:
    del modes
    if restriction is None:
        return True
    return valid_mana_spend_restriction(restriction)


def _without_activation_restriction(ability: Any, effect_text: str) -> str | None:
    marker = " Activate only "
    if marker not in effect_text:
        return effect_text
    base, tail = effect_text.rsplit(marker, 1)
    restriction = activation_restriction_spec(tail)
    if (
        restriction is None
        or restriction.sorcery_speed != bool(ability.sorcery_speed)
        or restriction.activation_limit != ability.activation_limit
        or restriction.conditions != tuple(ability.activation_conditions)
    ):
        return None
    return base.strip()


def _without_spend_restriction(
    effect_text: str,
    restriction: str | None,
) -> str | None:
    markers = tuple(
        value for value in _SPEND_RESTRICTION_MARKERS if value in effect_text
    )
    if not markers:
        return effect_text if restriction is None else None
    if (
        len(markers) != 1
        or effect_text.count(markers[0]) != 1
        or restriction is None
    ):
        return None
    marker = markers[0]
    base, tail = effect_text.split(marker, 1)
    if not restriction.startswith("mana-restriction-v1|"):
        if legacy_mana_spend_restriction_for_tail(marker, tail) != restriction:
            return None
    return base.strip()


def _supported_activation_constraints(ability: Any) -> bool:
    if ability.activation_limit not in {
        None,
        ActivationLimit.ONCE_PER_TURN,
        ActivationLimit.EXHAUST_ONCE,
    }:
        return False
    conditions = tuple(ability.activation_conditions)
    if not conditions:
        return True
    if len(conditions) != 1:
        return False
    condition = conditions[0]
    query = condition.query
    return bool(
        condition.kind is ActivationConditionKind.PUBLIC_QUERY_COUNT
        and query is not None
        and query.types_all == ("land",)
        and query.subtypes_any
    )


def compile_fixed_activated_mana_ability(
    ability: Any,
) -> FixedActivatedManaAbilitySpec | None:
    """Lower one parsed ability when this family's entire contract closes."""

    oracle_line = str(ability.oracle_line).strip()
    if oracle_line.startswith("(") and oracle_line.endswith(")"):
        # Parenthesized Oracle text is reminder text, not an executable printed
        # ability. Basic land types grant their intrinsic abilities through a
        # separate rules owner and must not be promoted by this family.
        return None
    effect_text = " ".join(str(ability.effect_text).split())
    effect_text = _without_activation_restriction(ability, effect_text)
    if effect_text is None:
        return None
    restriction = ability.mana_spend_restriction
    base_effect = _without_spend_restriction(effect_text, restriction)
    if base_effect is None:
        return None
    modes = fixed_mana_modes_from_effect(base_effect)
    if modes is None or not _restricted_modes_are_closed(
        restriction,
        modes,
    ):
        return None
    if (
        not ability.mana_ability
        or not ability.compiled_cost
        or tuple(ability.zones) != ("battlefield",)
        or ability.complex_symbols
        or ability.untap_source
        or ability.discard_source
        or ability.exile_source
        or ability.energy_payment
        or ability.loyalty_delta is not None
        or ability.choices
        or ability.uncompiled_costs
        or ability.sorcery_speed
        or ability.generic_reduction_per_legendary_creature
        or ability.builtin_semantic_key is not None
        or ability.target_schema is not None
        or ability.crew_threshold is not None
        or not _supported_activation_constraints(ability)
    ):
        return None
    return FixedActivatedManaAbilitySpec(
        ability_id=ability.ability_id,
        line_index=ability.line_index,
        oracle_line=ability.oracle_line,
        cost_text=ability.cost_text,
        effect_text=ability.effect_text,
        mana_cost=FrozenMap(
            {key: int(ability.mana.get(key, 0)) for key in MANA_COST_KEYS}
        ),
        tap_source=ability.tap_source,
        sacrifice_source=ability.sacrifice_source,
        life_payment=ability.life_payment,
        modes=modes,
        spend_restriction=restriction,
        activation_limit=ability.activation_limit,
        activation_conditions=tuple(ability.activation_conditions),
    )


def fixed_mana_handler_descriptor(
    spec: FixedActivatedManaAbilitySpec,
) -> dict[str, Any]:
    return {
        "handler_id": FIXED_MANA_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        "ability": spec.to_dict(),
    }


__all__ = [
    "FIXED_MANA_HANDLER_ID",
    "FixedActivatedManaAbilitySpec",
    "FixedManaAbilityError",
    "FixedManaMode",
    "compile_fixed_activated_mana_ability",
    "fixed_mana_handler_descriptor",
    "fixed_mana_modes_from_effect",
]
