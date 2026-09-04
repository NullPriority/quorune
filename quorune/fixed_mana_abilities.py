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
_FIXED_SPEND_RESTRICTIONS = {
    "artifact_spell_only",
    "creature_spell_only",
    "nonartifact_spell_prohibited",
}
_RESTRICTION_SUFFIXES = {
    " This mana can't be spent to cast a nonartifact spell.": (
        "nonartifact_spell_prohibited"
    ),
    " Spend this mana only to cast an artifact spell.": "artifact_spell_only",
    " Spend this mana only to cast a creature spell.": "creature_spell_only",
}


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
            type(self.spend_restriction) is not str
            or self.spend_restriction not in _FIXED_SPEND_RESTRICTIONS
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
        if "spend_restriction" in value:
            expected.add("spend_restriction")
        _exact_fields(value, expected, field="fixed mana ability")
        mana_cost = value["mana_cost"]
        modes = value["modes"]
        if not isinstance(mana_cost, Mapping):
            raise FixedManaAbilityError("Fixed mana activation cost must be an object")
        if not isinstance(modes, list) or any(
            not isinstance(mode, Mapping) for mode in modes
        ):
            raise FixedManaAbilityError("Fixed mana output modes must be an array")
        for field in ("ability_id", "oracle_line", "cost_text", "effect_text"):
            if not isinstance(value[field], str):
                raise FixedManaAbilityError(
                    f"Fixed mana ability {field} must be a string"
                )
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
    if restriction is None:
        return True
    bundles = tuple(mode.bundle for mode in modes)
    if restriction == "nonartifact_spell_prohibited":
        return bundles == (normalize_mana_bundle({"C": 1}),)
    if restriction in {"artifact_spell_only", "creature_spell_only"}:
        return bundles == tuple(
            normalize_mana_bundle({color: 1}) for color in "WUBRG"
        )
    return False


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
    restriction = ability.mana_spend_restriction
    base_effect = effect_text
    parsed_restriction = None
    for suffix, candidate in _RESTRICTION_SUFFIXES.items():
        if effect_text.endswith(suffix):
            base_effect = effect_text[: -len(suffix)]
            parsed_restriction = candidate
            break
    if restriction != parsed_restriction:
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
