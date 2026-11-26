from __future__ import annotations

"""Typed fixed-output activated mana abilities.

This is intentionally a closed grammar.  It compiles target-free, nonloyalty
activated mana abilities whose complete output is known before activation.
Dynamic quantities, open restrictions, conditional output, and unknown result
clauses remain outside this family. One closed fixed result uses a separate
version-two descriptor after the selected output.
"""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .activation_mana_cost import ActivationManaCostOption
from .activation_usage import ActivationLimit
from .activation_condition_model import (
    ActivationCondition,
    ActivationConditionKind,
    activation_restriction_spec,
)
from .rules.activation_counter_cost import SourceCounterRemovalCost
from .mana_restrictions import legacy_mana_spend_restriction_for_tail
from .mana_restriction_model import valid_mana_ability_spend_restriction
from .mana_spend_rules import decode_mana_spend_rule
from .fixed_mana_spend_templates import fixed_mana_spend_rule_template
from .replacement.immutable import FrozenMap, thaw_value
from .util import normalize_mana_bundle
from .fixed_mana_results import DISCARD_HAND_MANA_SOURCE, EXERT_MANA_SOURCE, FixedManaResultSpec, GAIN_CONTROLLER_LIFE, PLACE_MANA_POINT_COUNTER
from .rules.whole_hand_mana import HANDLER_ID as WHOLE_HAND_MANA_HANDLER_ID, whole_hand_mana_text
from .rules.forage_mana import HANDLER_ID as FORAGE_MANA_HANDLER_ID, forage_mana_modes, forage_mana_text, has_forage_mana_choice
from .rules.exert_mana import (
    EXERT_MANA_HANDLER_ID, EXERT_MANA_PRICE, EXERT_MANA_RESTRICTION, has_exact_exert_mana_text,
)


MANA_KEYS = ("W", "U", "B", "R", "G", "C")
MANA_COST_KEYS = ("GENERIC", *MANA_KEYS)
FIXED_MANA_HANDLER_ID = "ability.activated.mana.fixed-output.v1"
FIXED_MANA_RESULT_HANDLER_ID = 'ability.activated.mana.fixed-result.v2'
HAND_EXILE_MANA_HANDLER_ID = "ability.activated.mana.hand-exile-source.v6"
HAND_EXILE_MANA_CAPABILITY = "mana.activated.hand_exile_source"
HAND_EXILE_MANA_COSTS = frozenset({"exile this card from your hand", "exile this creature from your hand"})
HAND_EXILE_MANA_RULES = ("113.6j", "118.3c", "400.2", "406.3", "601.2g", "602.2a", "605.1a", "605.3a", "605.3b")
LUCK_COUNTER_MANA_HANDLER_ID = 'ability.activated.mana.luck-counter-color.v5'
LUCK_COUNTER_MANA_CAPABILITY = 'mana.activated.luck_counter_color_choice'
LUCK_COUNTER_MANA_OUTPUT = 'source_luck_counter_color_choice'
FIXED_MANA_SELECTED_COST_HANDLER_ID='ability.activated.mana.selected-cost.v3'
FIXED_MANA_SPEND_RULE_HANDLER_ID = "ability.activated.mana.spend-rule.v4"
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


def _selected_mana_choice_is_closed(choice):
    return (choice.kind=='sacrifice_one' and choice.count==1 and choice.fixed_zone_change_cost() is not None
        or choice.kind=='tap' and choice.fixed_tap_cost() is not None)


FIXED_MANA_HANDLER_CAPABILITIES = (
    "mana.activated.fixed_output",
    "mana.activated.restricted_fixed_output",
)


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


def _fixed_hybrid_filter_modes(options, modes) -> bool:
    if not isinstance(options, tuple) or not isinstance(modes, tuple) or len(options) != 2 or len(modes) != 3:
        return False
    colors = []
    for option in options:
        if not isinstance(option, ActivationManaCostOption) or option.life_payment or option.snow_payment:
            return False
        entries = [(color, amount) for color, amount in option.requirements.items() if amount]
        if len(entries) != 1 or entries[0][0] not in "WUBRG" or entries[0][1] != 1:
            return False
        colors.append(entries[0][0])
    left, right = colors
    if left == right or len({option.option_id for option in options}) != 2:
        return False
    expected = ({left: 2}, {left: 1, right: 1}, {right: 2})
    return all(isinstance(mode, FixedManaMode) for mode in modes) and all(
        any(mode.bundle == normalize_mana_bundle(bundle) for mode in modes) for bundle in expected
    )


def supports_hybrid_filter_price(ability, options, modes) -> bool:
    return (ability.mana_ability and ability.tap_source and ability.zones == ("battlefield",)
            and not any((sum(ability.mana.values()), ability.untap_source, ability.sacrifice_source,
                ability.exile_source, ability.discard_source, ability.life_payment, ability.energy_payment,
                ability.random_discard_payment, ability.choices, ability.sorcery_speed, ability.activation_limit,
                ability.activation_conditions, ability.mana_spend_restriction, ability.fixed_mana_result,
                ability.source_counter_removal_cost, ability.loyalty_delta is not None, ability.target_schema,
                ability.generic_reduction_per_legendary_creature, ability.uncompiled_costs))
            and _fixed_hybrid_filter_modes(options, modes))


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
    source_counter_removal_cost: SourceCounterRemovalCost | None = None
    result: FixedManaResultSpec | None = None
    choices: tuple[Any,...] = ()
    mana_cost_options: tuple[ActivationManaCostOption, ...] = ()
    luck_counter_color_choice: bool = False
    exile_source_from_hand: bool = False

    def _validate_choices(self):
        if not isinstance(self.choices,tuple):raise FixedManaAbilityError('Fixed mana choices must be immutable')
        if not self.choices:return
        from .abilities import CostChoice
        if has_forage_mana_choice(self):
            self._validate_forage_mana_choices(CostChoice(kind="forage"))
            return
        if (not isinstance(self.choices,tuple) or len(self.choices)!=1 or not isinstance(self.choices[0],CostChoice)
                or not _selected_mana_choice_is_closed(self.choices[0]) or self.result is not None):
            raise FixedManaAbilityError('Selected fixed mana requires one closed sacrifice or tap cost and no result rider')


    def _validate_forage_mana_choices(self, choice):
        if (self.choices != (choice,) or not self.tap_source
                or not forage_mana_text(self.cost_text, self.effect_text)
                or self.modes != forage_mana_modes() or any(self.mana_cost.values())
                or any((self.sacrifice_source, self.life_payment, self.result, self.spend_restriction,
                        self.activation_limit, self.activation_conditions, self.mana_cost_options,
                        self.source_counter_removal_cost, self.luck_counter_color_choice, self.exile_source_from_hand))):
            raise FixedManaAbilityError("Forage mana requires its exact tap, resource selection and two-color bundle")

    def _validate_point_result(self):
        if self.result is None or self.result.schema_version != 4:
            return
        if (not self.tap_source or self.cost_text.strip() != "{T}" or self.modes != (FixedManaMode(colorless=1),)
                or any((self.sacrifice_source, self.life_payment, self.choices, self.activation_limit,
                        self.activation_conditions, self.spend_restriction, self.source_counter_removal_cost,
                        self.mana_cost_options, sum(self.mana_cost.values())))):
            raise FixedManaAbilityError("A mana point counter requires one unmodified colorless tap ability")

    def _validate_exert_price(self):
        if self.result is None or self.result.kind != EXERT_MANA_SOURCE:
            return
        if (not self.tap_source or self.modes != (FixedManaMode(red=2),)
                or dict(self.mana_cost) != EXERT_MANA_PRICE or self.spend_restriction != EXERT_MANA_RESTRICTION
                or not has_exact_exert_mana_text(self.cost_text, self.effect_text)
                or any((self.sacrifice_source, self.life_payment, self.choices, self.activation_limit,
                        self.activation_conditions, self.source_counter_removal_cost,
                        self.mana_cost_options, self.luck_counter_color_choice, self.exile_source_from_hand))):
            raise FixedManaAbilityError("Exert mana requires its exact red tap price, output and spend rider")

    def _validate_whole_hand_mana_price(self):
        if self.result is None or self.result.kind != DISCARD_HAND_MANA_SOURCE:
            return
        if (self.tap_source or not self.sacrifice_source or not whole_hand_mana_text(self.cost_text, self.effect_text)
                or self.modes != fixed_mana_modes_from_effect("Add three mana of any one color.")
                or any(self.mana_cost.values()) or any((self.life_payment, self.choices, self.activation_limit,
                    self.activation_conditions, self.spend_restriction, self.source_counter_removal_cost,
                    self.mana_cost_options, self.luck_counter_color_choice, self.exile_source_from_hand))):
            raise FixedManaAbilityError("Whole-hand mana costs require their exact sacrifice and three-mana choice")

    def __post_init__(self) -> None:
        self._validate_choices()
        if self.result is not None and not isinstance(self.result,FixedManaResultSpec):
            raise FixedManaAbilityError('Fixed mana result must be typed or absent')
        if self.result is not None and self.sacrifice_source and self.result.schema_version not in {3, 6}:
            raise FixedManaAbilityError('Fixed mana result with source sacrifice requires independent information timing')
        if self.result is not None and self.result.schema_version == 2:
            cost = self.source_counter_removal_cost
            if cost is None or cost.schema_version != 1 or cost.counter_name != self.result.counter_name:
                raise FixedManaAbilityError('Counter-depletion results require their matching named source-counter cost')
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
        self._validate_point_result()
        self._validate_exert_price()
        self._validate_whole_hand_mana_price()
        if self.result is not None and self.result.schema_version == 3 and (
            self.cost_text != "{T}" or not self.tap_source or any(mana.values()) or len(self.modes) != 1
            or any((self.sacrifice_source, self.life_payment, self.spend_restriction, self.choices,
                    self.activation_limit, self.activation_conditions, self.source_counter_removal_cost,
                    self.mana_cost_options))
        ):
            raise FixedManaAbilityError("Printed fixed life gain requires an ordinary tap and one fixed mana bundle")
        if self.mana_cost_options and (
            not isinstance(self.mana_cost_options, tuple) or not self.tap_source or any(mana.values())
            or any((self.sacrifice_source, self.life_payment, self.choices, self.activation_limit,
                    self.activation_conditions, self.spend_restriction, self.result, self.source_counter_removal_cost))
            or not _fixed_hybrid_filter_modes(self.mana_cost_options, self.modes)
        ):
            raise FixedManaAbilityError("Priced fixed mana requires a closed hybrid tap filter")
        if self.spend_restriction is not None and (
            not valid_mana_ability_spend_restriction(self.spend_restriction)
        ):
            raise FixedManaAbilityError(
                "Fixed mana spending restriction is unsupported"
            )
        spend_rule = decode_mana_spend_rule(self.spend_restriction)
        if spend_rule is not None and spend_rule.requires_choice and self.sacrifice_source:
            raise FixedManaAbilityError("Source-bound mana spending requires an available designation source")
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
        if self.source_counter_removal_cost is not None and not isinstance(
            self.source_counter_removal_cost, SourceCounterRemovalCost
        ):
            raise FixedManaAbilityError(
                "Fixed mana source counter cost must be typed or null"
            )

        if type(self.luck_counter_color_choice) is not bool or (
            self.luck_counter_color_choice and (
                not self.tap_source or self.modes != (_symbol_bundle("{C}"),)
                or any(mana.values())
                or any((self.sacrifice_source, self.life_payment, self.choices, self.activation_limit,
                        self.activation_conditions, self.spend_restriction, self.result,
                        self.source_counter_removal_cost, self.mana_cost_options))
            )
        ):
            raise FixedManaAbilityError("Luck-counter color choice requires a tap-only colorless base mode")

        if type(self.exile_source_from_hand) is not bool:
            raise FixedManaAbilityError("Hand mana exile must be Boolean")
        if self.exile_source_from_hand and (
            any(mana.values()) or len(self.modes) != 1 or sum(self.modes[0].bundle.values()) != 1
            or any((self.tap_source, self.sacrifice_source, self.life_payment, self.choices,
                    self.activation_limit, self.activation_conditions, self.spend_restriction,
                    self.result, self.source_counter_removal_cost, self.mana_cost_options,
                    self.luck_counter_color_choice))
        ):
            raise FixedManaAbilityError("Hand exile mana requires only its source and one fixed mana")

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
        if self.luck_counter_color_choice:
            value['luck_counter_color_choice'] = True
        if self.exile_source_from_hand:
            value["exile_source_from_hand"] = True
        if self.result is not None:
            value['result']=self.result.to_dict()
        if self.choices:value['choices']=[choice.to_dict() for choice in self.choices]
        if self.mana_cost_options:
            value["mana_cost_options"] = [option.to_dict() for option in self.mana_cost_options]
        if self.activation_limit is not None:
            value["activation_limit"] = self.activation_limit.value
        if self.activation_conditions:
            value["activation_conditions"] = [
                condition.to_dict() for condition in self.activation_conditions
            ]
        if self.spend_restriction is not None:
            value["spend_restriction"] = self.spend_restriction
        if self.source_counter_removal_cost is not None:
            value["source_counter_removal_cost"] = (
                self.source_counter_removal_cost.to_dict()
            )
        return value

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FixedActivatedManaAbilitySpec":
        from .abilities import CostChoice
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
        if "source_counter_removal_cost" in value:
            expected.add("source_counter_removal_cost")
        if 'result' in value:
            expected.add('result')
        if 'choices' in value:expected.add('choices')
        if "mana_cost_options" in value:
            expected.add("mana_cost_options")
            if not isinstance(value["mana_cost_options"], list) or not value["mana_cost_options"]:
                raise FixedManaAbilityError("Declared hybrid filter prices must be a nonempty array")
        if "luck_counter_color_choice" in value:
            if value["luck_counter_color_choice"] is not True:
                raise FixedManaAbilityError("An absent luck-counter color choice must be omitted")
            expected.add("luck_counter_color_choice")
        if "exile_source_from_hand" in value:
            if value["exile_source_from_hand"] is not True:
                raise FixedManaAbilityError("An absent hand mana exile cost must be omitted")
            expected.add("exile_source_from_hand")
        _exact_fields(value, expected, field="fixed mana ability")
        mana_cost = value["mana_cost"]
        modes = value["modes"]
        activation_conditions = value.get("activation_conditions", [])
        raw_source_counter_cost = value.get("source_counter_removal_cost")
        raw_choices=value.get('choices',[])
        if not isinstance(raw_choices,list) or 'choices' in value and not raw_choices:raise FixedManaAbilityError('Fixed mana choices must be a nonempty array when declared')
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
        if raw_source_counter_cost is not None and not isinstance(
            raw_source_counter_cost, Mapping
        ):
            raise FixedManaAbilityError(
                "Fixed mana source counter cost must be an object"
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
            source_counter_removal_cost=(
                None
                if raw_source_counter_cost is None
                else SourceCounterRemovalCost.from_dict(
                    raw_source_counter_cost
                )
            ),
            result=FixedManaResultSpec.from_dict(value['result']) if 'result' in value else None,
            luck_counter_color_choice=value.get("luck_counter_color_choice", False),
            exile_source_from_hand=value.get("exile_source_from_hand", False),
            choices=tuple(CostChoice.from_dict(choice) for choice in raw_choices),
            mana_cost_options=tuple(ActivationManaCostOption.from_dict(option) for option in value.get("mana_cost_options", ())),
        )

    def to_activated_ability(self) -> Any:
        from .abilities import ActivatedAbility

        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            effect_text=self.effect_text,
            zones=("hand",) if self.exile_source_from_hand else ("battlefield",),
            mana=thaw_value(self.mana_cost),
            tap_source=self.tap_source,
            sacrifice_source=self.sacrifice_source,
            exile_source=self.exile_source_from_hand,
            life_payment=self.life_payment,
            mana_ability=True,
            fixed_mana_outputs=self.modes,
            dynamic_mana_output=LUCK_COUNTER_MANA_OUTPUT if self.luck_counter_color_choice else None,
            mana_spend_restriction=self.spend_restriction,
            activation_limit=self.activation_limit,
            activation_conditions=self.activation_conditions,
            source_counter_removal_cost=self.source_counter_removal_cost,
            fixed_mana_result=self.result,
            choices=self.choices,
            mana_cost_options=self.mana_cost_options,
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
    six_of_one_color = re.fullmatch(r"Add six \{(?P<color>[WUBRGC])\}\.", text, re.IGNORECASE)
    if six_of_one_color is not None:
        return (FixedManaMode.from_bundle({six_of_one_color["color"].upper(): 6}),)
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
    return valid_mana_ability_spend_restriction(restriction)


def _without_activation_restriction(ability: Any, effect_text: str, *, source_name='') -> str | None:
    marker = " Activate only "
    if marker not in effect_text:
        return effect_text
    base, tail = effect_text.rsplit(marker, 1)
    restriction = activation_restriction_spec(tail,source_name=source_name)
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
    return all(condition.kind is not ActivationConditionKind.UNSUPPORTED for condition in conditions)


def _luck_counter_color_output(ability, effect: str, source_name: str) -> bool:
    from .rules.source_references import SourceReferenceSpec

    named = "|" + SourceReferenceSpec(source_name).regex_pattern if source_name else ""
    pattern = (
        rf"Add \{{C\}}\. If (?:this (?:land|permanent){named}) has a luck counter on it, "
        r"instead add one mana of any color\."
    )
    return bool(
        re.fullmatch(pattern, effect, re.I)
        and ability.tap_source and not any(ability.mana.values())
        and not any((ability.sacrifice_source, ability.life_payment, ability.choices,
                     ability.activation_limit, ability.activation_conditions, ability.mana_cost_options,
                     ability.source_counter_removal_cost, ability.mana_spend_restriction))
    )


def is_hand_exile_mana_text(cost_text: str, effect_text: str) -> bool:
    return (cost_text.casefold() in HAND_EXILE_MANA_COSTS
            and re.fullmatch(r"Add \{[WUBRGC]\}\.", effect_text, re.IGNORECASE) is not None)


def _fixed_mana_result_from_tail(ability, tail: str, source_name: str, *, output, restriction) -> FixedManaResultSpec | None:
    from .rules.source_references import SourceReferenceSpec

    gain = re.fullmatch(r"\s*You gain (?P<amount>[1-9]|1[0-9]|20) life\.\s*", tail, re.I)
    if gain is not None:
        return FixedManaResultSpec(GAIN_CONTROLLER_LIFE, int(gain["amount"]), schema_version=3)
    named = "|" + SourceReferenceSpec(source_name).regex_pattern if source_name else ""
    subject = rf"(?:this (?:artifact|creature|land|permanent){named})"
    if re.fullmatch(rf"\s*Put a point counter on {subject}\.\s*", tail, re.I):
        from .rules.mana_point_result import is_unmodified_point_mana

        if not is_unmodified_point_mana(ability, restriction, output.strip()):
            return None
        return FixedManaResultSpec(PLACE_MANA_POINT_COUNTER, 1, schema_version=4, counter_name="point")
    damage = re.fullmatch(rf"\s*{subject} deals (?P<amount>[1-9][0-9]*) damage to you\.\s*", tail, re.I)
    if damage is not None:
        return FixedManaResultSpec("damage_controller", int(damage["amount"]))
    if re.fullmatch(rf"\s*{subject} doesn['’]t untap during your next untap step\.\s*", tail, re.I):
        return FixedManaResultSpec("skip_actor_untap")
    depletion = re.fullmatch(
        rf"\s*If there are no (?P<counter>[a-z][a-z -]*) counters on {subject}, sacrifice it\.\s*",
        tail, re.I,
    )
    counter_cost = ability.source_counter_removal_cost
    if (depletion is not None and counter_cost is not None and counter_cost.schema_version == 1
            and counter_cost.counter_name == depletion["counter"].casefold()):
        return FixedManaResultSpec("sacrifice_if_no_counters", schema_version=2, counter_name=counter_cost.counter_name)
    return None


def _compile_exert_mana_ability(ability):
    if (not has_exact_exert_mana_text(ability.cost_text, ability.effect_text)
            or ability.zones != ("battlefield",) or not ability.mana_ability or not ability.tap_source
            or dict(ability.mana) != EXERT_MANA_PRICE
            or tuple(ability.uncompiled_costs) not in ((), ("Exert this land",))
            or any((ability.complex_symbols, ability.untap_source, ability.discard_source,
                    ability.sacrifice_source, ability.exile_source, ability.life_payment, ability.energy_payment,
                    ability.random_discard_payment, ability.choices, ability.activation_limit, ability.activation_conditions,
                    ability.source_counter_removal_cost, ability.loyalty_delta is not None, ability.target_schema,
                    ability.sorcery_speed, ability.builtin_semantic_key, ability.mana_cost_options,
                    ability.source_counter_placement_cost, ability.waterbend, ability.forecast,
                    ability.ability_local_mana_reduction, ability.mill_library_cost))):
        return None
    return FixedActivatedManaAbilitySpec(
        ability_id=ability.ability_id, line_index=ability.line_index, oracle_line=ability.oracle_line,
        cost_text=ability.cost_text, effect_text=ability.effect_text, mana_cost=FrozenMap(ability.mana),
        tap_source=True, sacrifice_source=False, life_payment=0, modes=(FixedManaMode(red=2),),
        spend_restriction=EXERT_MANA_RESTRICTION, result=FixedManaResultSpec(EXERT_MANA_SOURCE, schema_version=5),
    )


def _compile_forage_mana_ability(ability):
    from .rules.forage import validate_forage_activation

    try:
        validate_forage_activation(ability)
    except ValueError:
        return None
    if not ability.mana_ability or any((ability.activation_limit, ability.activation_conditions,
            ability.target_schema, ability.sorcery_speed, ability.builtin_semantic_key,
            ability.mana_spend_restriction, ability.source_counter_placement_cost,
            ability.random_discard_payment, ability.mill_library_cost, ability.granting_attachment_payment,
            ability.forecast, ability.ability_local_mana_reduction, ability.variable_colored_reveal_price)):
        return None
    return FixedActivatedManaAbilitySpec(
        ability_id=ability.ability_id, line_index=ability.line_index, oracle_line=ability.oracle_line,
        cost_text=ability.cost_text, effect_text=ability.effect_text, mana_cost=FrozenMap(ability.mana),
        tap_source=True, sacrifice_source=False, life_payment=0, modes=forage_mana_modes(), choices=ability.choices,
    )


def _compile_whole_hand_mana_ability(ability):
    if (not whole_hand_mana_text(ability.cost_text, ability.effect_text)
            or ability.zones != ("battlefield",) or not ability.mana_ability or not ability.sacrifice_source
            or tuple(ability.uncompiled_costs) not in ((), ("Discard your hand",))
            or any(ability.mana.values()) or any((ability.tap_source, ability.untap_source, ability.exile_source,
                ability.discard_source, ability.complex_symbols, ability.life_payment, ability.energy_payment,
                ability.random_discard_payment, ability.choices, ability.activation_limit, ability.activation_conditions,
                ability.source_counter_removal_cost, ability.loyalty_delta is not None, ability.target_schema,
                ability.sorcery_speed, ability.builtin_semantic_key, ability.mana_cost_options,
                ability.source_counter_placement_cost, ability.waterbend, ability.forecast,
                ability.ability_local_mana_reduction, ability.mill_library_cost))):
        return None
    return FixedActivatedManaAbilitySpec(
        ability_id=ability.ability_id, line_index=ability.line_index, oracle_line=ability.oracle_line,
        cost_text=ability.cost_text, effect_text=ability.effect_text, mana_cost=FrozenMap(ability.mana),
        tap_source=False, sacrifice_source=True, life_payment=0,
        modes=fixed_mana_modes_from_effect("Add three mana of any one color."),
        result=FixedManaResultSpec(DISCARD_HAND_MANA_SOURCE, schema_version=6),
    )


def compile_fixed_activated_mana_ability(
    ability: Any,
    *, source_name: str = '',
) -> FixedActivatedManaAbilitySpec | None:
    """Lower one parsed ability when this family's entire contract closes."""

    if has_forage_mana_choice(ability):
        return _compile_forage_mana_ability(ability)
    if exert := _compile_exert_mana_ability(ability):
        return exert
    if whole := _compile_whole_hand_mana_ability(ability):
        return whole
    if getattr(ability, 'mill_library_cost', 0):
        return None
    oracle_line = str(ability.oracle_line).strip()
    if oracle_line.startswith("(") and oracle_line.endswith(")"):
        # Parenthesized Oracle text is reminder text, not an executable printed
        # ability. Basic land types grant their intrinsic abilities through a
        # separate rules owner and must not be promoted by this family.
        return None
    effect_text = " ".join(str(ability.effect_text).split())
    effect_text = _without_activation_restriction(ability, effect_text,source_name=source_name)
    if effect_text is None:
        return None
    typed_spend = fixed_mana_spend_rule_template(effect_text)
    if typed_spend is None:
        restriction = ability.mana_spend_restriction
        base_effect = _without_spend_restriction(effect_text, restriction)
    else:
        base_effect, restriction = typed_spend
    if base_effect is None:
        return None
    result = None
    luck_counter_color_choice = _luck_counter_color_output(ability, base_effect, source_name)
    modes = fixed_mana_modes_from_effect("Add {C}." if luck_counter_color_choice else base_effect)
    if modes is None:
        output, separator, tail = base_effect.partition(".")
        result = _fixed_mana_result_from_tail(ability, tail, source_name, output=output, restriction=restriction) if separator else None
        if result is not None:
            modes = fixed_mana_modes_from_effect(output.strip() + ".")
    if modes is None or not _restricted_modes_are_closed(
        restriction,
        modes,
    ):
        return None
    hand_exile = (
        ability.zones == ("hand",) and ability.exile_source
        and is_hand_exile_mana_text(ability.cost_text, ability.effect_text)
    )
    if hand_exile and any((ability.activation_limit, ability.activation_conditions,
                           ability.source_counter_removal_cost, ability.mana_cost_options,
                           ability.boast, ability.forecast, ability.saddle_threshold,
                           ability.waterbend, ability.source_counter_placement_cost)):
        return None
    if result is not None and result.kind == GAIN_CONTROLLER_LIFE and (
        ability.cost_text != "{T}" or not ability.tap_source or any(ability.mana.values()) or len(modes) != 1
        or any((ability.sacrifice_source, ability.life_payment, restriction, ability.choices,
                ability.activation_limit, ability.activation_conditions, ability.source_counter_removal_cost,
                ability.mana_cost_options))
    ):
        return None
    if (
        not ability.mana_ability
        or not ability.compiled_cost
        or ability.zones != (("hand",) if hand_exile else ("battlefield",))
        or ability.complex_symbols
        or ability.untap_source
        or ability.discard_source
        or ability.exile_source != hand_exile
        or ability.energy_payment
        or ability.random_discard_payment
        or ability.loyalty_delta is not None
        or (ability.choices and (len(ability.choices)!=1 or not _selected_mana_choice_is_closed(ability.choices[0])))
        or ability.uncompiled_costs
        or ability.sorcery_speed
        or ability.generic_reduction_per_legendary_creature
        or (ability.builtin_semantic_key is not None and not (
            result is not None and result.kind=='damage_controller'
            and ability.builtin_semantic_key==f'builtin:mana-result-damage-controller:{result.amount}'))
        or (result is not None and (ability.sacrifice_source or ability.choices))
        or (typed_spend is not None and (result is not None or ability.choices
            or ability.sacrifice_source and decode_mana_spend_rule(restriction).requires_choice))
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
        source_counter_removal_cost=ability.source_counter_removal_cost,
        result=result,
        choices=tuple(ability.choices),
        mana_cost_options=ability.mana_cost_options,
        luck_counter_color_choice=luck_counter_color_choice,
        exile_source_from_hand=hand_exile,
    )


def fixed_mana_handler_descriptor(
    spec: FixedActivatedManaAbilitySpec,
) -> dict[str, Any]:
    if has_forage_mana_choice(spec):
        handler_id, version = FORAGE_MANA_HANDLER_ID, 8
    elif spec.result is not None and spec.result.kind == EXERT_MANA_SOURCE:
        handler_id, version = EXERT_MANA_HANDLER_ID, 7
    elif spec.exile_source_from_hand:
        handler_id, version = HAND_EXILE_MANA_HANDLER_ID, 6
    elif spec.luck_counter_color_choice:
        handler_id, version = LUCK_COUNTER_MANA_HANDLER_ID, 5
    elif decode_mana_spend_rule(spec.spend_restriction) is not None:
        handler_id, version = FIXED_MANA_SPEND_RULE_HANDLER_ID, 4
    elif spec.choices:
        handler_id, version = FIXED_MANA_SELECTED_COST_HANDLER_ID, 3
    elif spec.result is not None:
        if spec.result.kind == DISCARD_HAND_MANA_SOURCE:
            handler_id, version = WHOLE_HAND_MANA_HANDLER_ID, 9
        else:
            handler_id, version = FIXED_MANA_RESULT_HANDLER_ID, 2
    else:
        handler_id, version = FIXED_MANA_HANDLER_ID, 1
    return {
        "handler_id": handler_id,
        "schema_version": version,
        "event": "activate",
        "ability": spec.to_dict(),
    }


__all__ = [
    "FIXED_MANA_HANDLER_CAPABILITIES",
    "FIXED_MANA_HANDLER_ID",
    "FixedActivatedManaAbilitySpec",
    "FixedManaAbilityError",
    "FixedManaMode",
    "compile_fixed_activated_mana_ability",
    "fixed_mana_handler_descriptor",
    "fixed_mana_modes_from_effect",
]
