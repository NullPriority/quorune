from __future__ import annotations

"""Conservative extraction of explicit activated abilities from Oracle text.

This module does *not* try to interpret arbitrary Magic prose.  It identifies
colon-form activated abilities, derives ordinary mana and a small set of
objective nonmana costs, and records anything else as uncompiled.  The rules
kernel can therefore expose Channel and other zone-specific abilities without
letting a pilot invent a cheaper cost or mutate state directly.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Iterable, Mapping, Sequence

from .activation_usage import ActivationLimit
from .activation_condition_model import (
    ActivationCondition,
    ActivationConditionKind,
    activation_restriction_spec,
)
from .activated_ability_descriptor import validate_activated_ability_descriptor
from .replacement.immutable import FrozenMap, thaw_value
from .color_set_mana_abilities import ColorSetActivatedManaAbilitySpec
from .fixed_mana_abilities import FixedManaMode
from .rules.source_references import SourceReferenceSpec
from .util import mana_cost_to_vector, normalize_mana_bundle, parse_mana_symbols

_ACTIVATE_ONLY_SORCERY = re.compile(
    r"(?:activate|craft) only as a sorcery", re.IGNORECASE
)
_PAY_LIFE = re.compile(r"^pay\s+(\d+)\s+life$", re.IGNORECASE)
_PAY_ENERGY = re.compile(
    r"^pay\s+(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten)$",
    re.IGNORECASE,
)
_SACRIFICE_CHOICE = re.compile(
    r"^sacrifice\s+(?:(?P<another>another)\s+|(?P<count>a|an|one|two|three|\d+)\s+)"
    r"(?P<kind>creature|artifact|enchantment|land|permanent)s?(?:\s+you\s+control)?$",
    re.IGNORECASE,
)
_DISCARD_CHOICE = re.compile(
    r"^discard\s+(?P<count>a|an|one|two|three|\d+)\s+"
    r"(?:(?P<kind>creature|land|artifact|enchantment|instant|sorcery|planeswalker)\s+)?card(?:s)?$",
    re.IGNORECASE,
)
_RETURN_CHOICE = re.compile(
    r"^return\s+(?:a|an|one)\s+"
    r"(?P<kind>creature|artifact|enchantment|land|permanent|"
    r"plains|island|swamp|mountain|forest)"
    r"(?:\s+you\s+control)?\s+to\s+its\s+owner'?s\s+hand$",
    re.IGNORECASE,
)
_LIBRARY_LAND_SEARCH = re.compile(
    r"search your library for (?:an?|up to one) "
    r"(?P<types>[A-Za-z ]+?(?: or [A-Za-z ]+?)*) card, "
    r"put (?:it|that card) onto the battlefield",
    re.IGNORECASE,
)
_LIBRARY_MOVEMENT_SIGNAL = re.compile(
    r"\b(?:draw|draws|mill|mills|surveil|explore|discover|cascade|manifest|"
    r"cloak|recruit|connive|learn|ripple|hideaway|seek)\b",
    re.IGNORECASE,
)
_SUPPORTED_LIBRARY_LAND_SEARCH_TYPES = frozenset(
    {
        "basic land",
        "plains",
        "island",
        "swamp",
        "mountain",
        "forest",
    }
)

_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}
_NUMBER_WORDS.update(
    {
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
)


@dataclass(frozen=True, slots=True)
class CostChoice:
    kind: str
    count: int = 1
    zone: str = "battlefield"
    card_type: str | None = None
    another: bool = False
    predicate: FrozenMap | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("cost choice kind must be a nonempty string")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("cost choice count must be a positive integer")
        if not isinstance(self.zone, str) or not self.zone.strip():
            raise ValueError("cost choice zone must be a nonempty string")
        if self.card_type is not None and (
            not isinstance(self.card_type, str) or not self.card_type.strip()
        ):
            raise ValueError("cost choice card_type must be null or nonempty")
        if type(self.another) is not bool:
            raise ValueError("cost choice another flag must be boolean")
        if self.predicate is not None:
            if not isinstance(self.predicate, Mapping):
                raise ValueError("cost choice predicate must be an object")
            object.__setattr__(self, "predicate", FrozenMap(self.predicate))
            tap_cost = self.fixed_tap_cost()
            if tap_cost is None:
                if self.count != 1 or self.card_type is not None:
                    raise ValueError(
                        "typed zone-change cost choices require one exact query"
                    )
                cost = self.fixed_zone_change_cost()
                if cost is None or self.zone != cost.origin_zone:
                    raise ValueError(
                        "typed zone-change cost choice origin is inconsistent"
                    )
                if self.another and cost.operation != "sacrifice_one":
                    raise ValueError(
                        "another is supported only for typed sacrifice costs"
                    )

    def fixed_zone_change_cost(self) -> Any | None:
        """Return the shared closed zone-change cost represented by this choice."""

        if self.predicate is None:
            return None
        from .additional_cost_vocabulary import (
            FIXED_ZONE_CHANGE_COST_CONTRACTS,
            ZONE_CHANGE_COST_KIND,
        )
        from .rules.casting_additional_costs import (
            FixedZoneChangeAdditionalCost,
        )

        contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(self.kind)
        if contract is None:
            return None
        return FixedZoneChangeAdditionalCost.from_descriptor(
            {
                "schema_version": 1,
                "kind": ZONE_CHANGE_COST_KIND,
                "operation": self.kind,
                "count": 1,
                "choice_field": contract[2],
                "predicate": thaw_value(self.predicate),
            }
        )

    def fixed_tap_cost(self) -> Any | None:
        """Return the shared closed selected-permanent tap cost, if present."""

        if self.predicate is None:
            return None
        from .rules.activation_costs import (
            FIXED_TAP_ACTIVATION_COST_KIND,
            FixedTapActivationCost,
        )

        if self.kind != FIXED_TAP_ACTIVATION_COST_KIND:
            return None
        return FixedTapActivationCost.from_descriptor(
            {
                "schema_version": 1,
                "kind": self.kind,
                "count": self.count,
                "zone": self.zone,
                "another": self.another,
                "predicate": thaw_value(self.predicate),
            }
        )

    def compact(self) -> dict[str, Any]:
        result: dict[str, Any] = {"k": self.kind, "n": self.count, "z": self.zone}
        if self.card_type:
            result["t"] = self.card_type
        if self.another:
            result["other"] = 1
        if self.predicate is not None:
            result["q"] = thaw_value(self.predicate)
        return result

    def to_dict(self) -> dict[str, Any]:
        result = {
            "kind": self.kind,
            "count": self.count,
            "zone": self.zone,
            "card_type": self.card_type,
            "another": self.another,
        }
        if self.predicate is not None:
            result["predicate"] = thaw_value(self.predicate)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CostChoice":
        expected = {"kind", "count", "zone", "card_type", "another"}
        if not isinstance(value, Mapping) or set(value) not in {
            frozenset(expected),
            frozenset((*expected, "predicate")),
        }:
            raise ValueError("cost choices use a closed schema")
        if "predicate" in value and value["predicate"] is None:
            raise ValueError(
                "typed cost choice predicates must be nonnull objects"
            )
        return cls(
            kind=value["kind"],
            count=value["count"],
            zone=value["zone"],
            card_type=value["card_type"],
            another=value["another"],
            predicate=(
                FrozenMap(value["predicate"])
                if "predicate" in value
                else None
            ),
        )


_DYNAMIC_MANA_OUTPUTS = frozenset({"opponent_land_colors"})
_MANA_SPEND_RESTRICTIONS = frozenset(
    {
        "artifact_spell_only",
        "artifact_spell_or_ability",
        "creature_spell_only",
        "nonartifact_spell_prohibited",
        "legendary_spell_uncounterable",
    }
)


@dataclass(frozen=True, slots=True)
class ActivatedAbility:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    effect_text: str
    zones: tuple[str, ...]
    mana: Mapping[str, int]
    complex_symbols: tuple[str, ...] = ()
    tap_source: bool = False
    untap_source: bool = False
    discard_source: bool = False
    sacrifice_source: bool = False
    exile_source: bool = False
    life_payment: int = 0
    energy_payment: int = 0
    loyalty_delta: int | None = None
    choices: tuple[CostChoice, ...] = ()
    uncompiled_costs: tuple[str, ...] = ()
    mana_ability: bool = False
    sorcery_speed: bool = False
    generic_reduction_per_legendary_creature: int = 0
    builtin_semantic_key: str | None = None
    target_schema: FrozenMap | None = None
    crew_threshold: int | None = None
    fixed_mana_outputs: tuple[FixedManaMode, ...] = ()
    color_set_mana_output: ColorSetActivatedManaAbilitySpec | None = None
    activation_limit: ActivationLimit | None = None
    library_search_types: tuple[str, ...] = ()
    activation_conditions: tuple[ActivationCondition, ...] = ()
    dynamic_mana_output: str | None = None
    mana_spend_restriction: str | None = None

    def __post_init__(self) -> None:
        _validate_ability_identity_and_cost(self)
        _validate_ability_sequences_and_scalars(self)
        _normalize_and_validate_ability_descriptors(self)
        _validate_ability_closed_vocabulary(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "effect_text": self.effect_text,
            "zones": list(self.zones),
            "mana": thaw_value(self.mana),
            "complex_symbols": list(self.complex_symbols),
            "tap_source": self.tap_source,
            "untap_source": self.untap_source,
            "discard_source": self.discard_source,
            "sacrifice_source": self.sacrifice_source,
            "exile_source": self.exile_source,
            "life_payment": self.life_payment,
            "energy_payment": self.energy_payment,
            "loyalty_delta": self.loyalty_delta,
            "choices": [choice.to_dict() for choice in self.choices],
            "uncompiled_costs": list(self.uncompiled_costs),
            "mana_ability": self.mana_ability,
            "sorcery_speed": self.sorcery_speed,
            "generic_reduction_per_legendary_creature": (
                self.generic_reduction_per_legendary_creature
            ),
            "builtin_semantic_key": self.builtin_semantic_key,
            "target_schema": (
                None
                if self.target_schema is None
                else thaw_value(self.target_schema)
            ),
            "crew_threshold": self.crew_threshold,
            "fixed_mana_outputs": [
                mode.to_dict() for mode in self.fixed_mana_outputs
            ],
            "color_set_mana_output": (
                None
                if self.color_set_mana_output is None
                else self.color_set_mana_output.to_dict()
            ),
            "activation_limit": (
                None
                if self.activation_limit is None
                else self.activation_limit.value
            ),
            "library_search_types": list(self.library_search_types),
            "activation_conditions": [
                condition.to_dict() for condition in self.activation_conditions
            ],
            "dynamic_mana_output": self.dynamic_mana_output,
            "mana_spend_restriction": self.mana_spend_restriction,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActivatedAbility":
        value = validate_activated_ability_descriptor(value)
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            effect_text=value["effect_text"],
            zones=tuple(value["zones"]),
            mana=FrozenMap(value["mana"]),
            complex_symbols=tuple(value["complex_symbols"]),
            tap_source=value["tap_source"],
            untap_source=value["untap_source"],
            discard_source=value["discard_source"],
            sacrifice_source=value["sacrifice_source"],
            exile_source=value["exile_source"],
            life_payment=value["life_payment"],
            energy_payment=value["energy_payment"],
            loyalty_delta=value["loyalty_delta"],
            choices=tuple(
                CostChoice.from_dict(choice) for choice in value["choices"]
            ),
            uncompiled_costs=tuple(value["uncompiled_costs"]),
            mana_ability=value["mana_ability"],
            sorcery_speed=value["sorcery_speed"],
            generic_reduction_per_legendary_creature=value[
                "generic_reduction_per_legendary_creature"
            ],
            builtin_semantic_key=value["builtin_semantic_key"],
            target_schema=(
                None
                if value["target_schema"] is None
                else FrozenMap(value["target_schema"])
            ),
            crew_threshold=value["crew_threshold"],
            fixed_mana_outputs=tuple(
                FixedManaMode.from_dict(mode)
                for mode in value["fixed_mana_outputs"]
            ),
            color_set_mana_output=(
                None
                if value["color_set_mana_output"] is None
                else ColorSetActivatedManaAbilitySpec.from_dict(
                    value["color_set_mana_output"]
                )
            ),
            activation_limit=value["activation_limit"],
            library_search_types=tuple(value["library_search_types"]),
            activation_conditions=tuple(
                ActivationCondition.from_dict(condition)
                for condition in value["activation_conditions"]
            ),
            dynamic_mana_output=value["dynamic_mana_output"],
            mana_spend_restriction=value["mana_spend_restriction"],
        )

    @property
    def compiled_cost(self) -> bool:
        return not self.complex_symbols and not self.uncompiled_costs

    def compact(self, *, source_ref: str, zone: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "s": source_ref,
            "z": zone,
            "a": self.ability_id,
            "i": self.line_index,
        }
        mana = {key: value for key, value in self.mana.items() if value}
        if mana:
            result["m"] = mana
        if self.tap_source:
            result["tap"] = 1
        if self.discard_source:
            result["discard_self"] = 1
        if self.sacrifice_source:
            result["sac_self"] = 1
        if self.exile_source:
            result["exile_self"] = 1
        if self.life_payment:
            result["life"] = self.life_payment
        if self.energy_payment:
            result["energy"] = self.energy_payment
        if self.loyalty_delta is not None:
            result["loyalty"] = self.loyalty_delta
        if self.choices:
            result["choose_cost"] = [choice.compact() for choice in self.choices]
        if not self.compiled_cost:
            result["needs_rules"] = 1
        if self.mana_ability:
            result["mana_ability"] = 1
        if self.sorcery_speed:
            result["sorcery"] = 1
        if self.generic_reduction_per_legendary_creature:
            result["legend_discount"] = self.generic_reduction_per_legendary_creature
        if self.target_schema is not None:
            result["target_schema"] = thaw_value(self.target_schema)
        if self.crew_threshold is not None:
            result["crew"] = self.crew_threshold
        if self.activation_limit is not None:
            result["activation_limit"] = self.activation_limit.value
        if self.library_search_types:
            result["search_types"] = list(self.library_search_types)
        return result


def _validate_ability_identity_and_cost(ability: ActivatedAbility) -> None:
    for field_name in ("ability_id", "oracle_line", "cost_text", "effect_text"):
        value = getattr(ability, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"activated ability {field_name} must be nonempty"
            )
    if type(ability.line_index) is not int or ability.line_index < 0:
        raise ValueError("activated ability line_index must be nonnegative")
    if (
        not isinstance(ability.zones, tuple)
        or not ability.zones
        or any(
            not isinstance(zone, str) or not zone.strip()
            for zone in ability.zones
        )
        or len(ability.zones) != len(set(ability.zones))
    ):
        raise ValueError(
            "activated ability zones must be unique nonempty strings"
        )
    mana_keys = ("GENERIC", "W", "U", "B", "R", "G", "C")
    if not isinstance(ability.mana, Mapping) or not set(ability.mana).issubset(
        mana_keys
    ):
        raise ValueError("activated ability mana contains unsupported keys")
    if any(
        type(amount) is not int or amount < 0
        for amount in ability.mana.values()
    ):
        raise ValueError(
            "activated ability mana amounts must be nonnegative integers"
        )
    object.__setattr__(
        ability,
        "mana",
        FrozenMap({key: int(ability.mana.get(key, 0)) for key in mana_keys}),
    )


def _validate_ability_sequences_and_scalars(ability: ActivatedAbility) -> None:
    for field_name in (
        "complex_symbols",
        "uncompiled_costs",
        "library_search_types",
    ):
        values = getattr(ability, field_name)
        if not isinstance(values, tuple) or any(
            not isinstance(value, str) or not value.strip()
            for value in values
        ):
            raise ValueError(
                f"activated ability {field_name} must contain strings"
            )
    if not isinstance(ability.choices, tuple) or any(
        not isinstance(choice, CostChoice) for choice in ability.choices
    ):
        raise ValueError("activated ability choices must be typed")
    if not isinstance(ability.activation_conditions, tuple) or any(
        not isinstance(condition, ActivationCondition)
        for condition in ability.activation_conditions
    ):
        raise ValueError("activation_conditions must contain typed predicates")
    for field_name in (
        "tap_source",
        "untap_source",
        "discard_source",
        "sacrifice_source",
        "exile_source",
        "mana_ability",
        "sorcery_speed",
    ):
        if type(getattr(ability, field_name)) is not bool:
            raise ValueError(f"activated ability {field_name} must be boolean")
    for field_name in (
        "life_payment",
        "energy_payment",
        "generic_reduction_per_legendary_creature",
    ):
        value = getattr(ability, field_name)
        if type(value) is not int or value < 0:
            raise ValueError(
                f"activated ability {field_name} must be nonnegative"
            )


def _normalize_and_validate_ability_descriptors(
    ability: ActivatedAbility,
) -> None:
    for field_name in ("loyalty_delta", "crew_threshold"):
        value = getattr(ability, field_name)
        if value is not None and type(value) is not int:
            raise ValueError(
                f"activated ability {field_name} must be an integer or null"
            )
    if ability.crew_threshold is not None and ability.crew_threshold < 0:
        raise ValueError("activated ability crew_threshold cannot be negative")
    if ability.target_schema is not None and not isinstance(
        ability.target_schema, FrozenMap
    ):
        object.__setattr__(
            ability, "target_schema", FrozenMap(ability.target_schema)
        )
    if not isinstance(ability.fixed_mana_outputs, tuple) or any(
        not isinstance(mode, FixedManaMode)
        for mode in ability.fixed_mana_outputs
    ):
        raise ValueError("fixed_mana_outputs must contain typed modes")
    if ability.color_set_mana_output is not None and not isinstance(
        ability.color_set_mana_output, ColorSetActivatedManaAbilitySpec
    ):
        raise ValueError(
            "color_set_mana_output must be a typed color-set descriptor"
        )
    if ability.activation_limit is not None and not isinstance(
        ability.activation_limit, ActivationLimit
    ):
        try:
            object.__setattr__(
                ability,
                "activation_limit",
                ActivationLimit(ability.activation_limit),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("activation_limit is unsupported") from exc


def _validate_ability_closed_vocabulary(ability: ActivatedAbility) -> None:
    if ability.builtin_semantic_key is not None and (
        not isinstance(ability.builtin_semantic_key, str)
        or not ability.builtin_semantic_key.strip()
    ):
        raise ValueError(
            "activated ability builtin_semantic_key must be null or nonempty"
        )
    if (
        len(ability.library_search_types)
        != len(set(ability.library_search_types))
        or any(
            value not in _SUPPORTED_LIBRARY_LAND_SEARCH_TYPES
            for value in ability.library_search_types
        )
    ):
        raise ValueError("library_search_types are unsupported")
    if ability.dynamic_mana_output is not None and (
        not isinstance(ability.dynamic_mana_output, str)
        or ability.dynamic_mana_output not in _DYNAMIC_MANA_OUTPUTS
    ):
        raise ValueError("dynamic_mana_output is unsupported")
    if ability.mana_spend_restriction is not None and (
        not isinstance(ability.mana_spend_restriction, str)
        or ability.mana_spend_restriction not in _MANA_SPEND_RESTRICTIONS
    ):
        raise ValueError("mana_spend_restriction is unsupported")


def _number(value: str) -> int:
    return _NUMBER_WORDS.get(value.casefold(), int(value) if value.isdigit() else 1)


def _strip_keyword_prefix(cost_text: str) -> tuple[str, str | None]:
    """Return the actual cost text and an optional named ability prefix."""
    if "—" in cost_text:
        prefix, remainder = cost_text.split("—", 1)
        if prefix.strip() and remainder.strip():
            return remainder.strip(), prefix.strip()
    if "-" in cost_text:
        # Oracle uses an em dash, but tolerate normalized text while avoiding
        # subtraction/negative loyalty symbols.
        prefix, remainder = cost_text.split("-", 1)
        if prefix.strip().isalpha() and remainder.strip():
            return remainder.strip(), prefix.strip()
    return cost_text.strip(), None


def _ability_zones(
    *,
    line: str,
    cost_text: str,
    effect_text: str,
    keyword_prefix: str | None,
    keywords: Iterable[str],
) -> tuple[str, ...]:
    lower_line = line.casefold()
    lower_cost = cost_text.casefold()
    keyword_set = {keyword.casefold() for keyword in keywords}
    prefix = (keyword_prefix or "").casefold()

    if prefix == "channel" or ("channel" in keyword_set and lower_line.startswith("channel")):
        return ("hand",)
    if prefix == "cycling":
        return ("hand",)
    if "discard this card" in lower_cost and (prefix or "channel" in keyword_set):
        return ("hand",)
    if "exile this card from your graveyard" in lower_cost:
        return ("graveyard",)
    if re.search(
        r"activate (?:this ability )?only (?:from|if this card is in) "
        r"(?:your|a) graveyard",
        lower_line,
    ):
        return ("graveyard",)
    if any(keyword in keyword_set for keyword in {"unearth", "encore", "scavenge", "embalm", "eternalize"}):
        if any(lower_line.startswith(keyword) for keyword in {"unearth", "encore", "scavenge", "embalm", "eternalize"}):
            return ("graveyard",)
    if "from exile" in lower_line and "activate" in lower_line:
        return ("exile",)
    return ("battlefield",)


def _split_cost_clauses(cost_text: str) -> list[str]:
    # Explicit activated costs conventionally use comma-separated clauses.
    # Oracle card names in self-discard costs are represented as "this card",
    # so a conservative split is preferable to accepting an opaque full cost.
    return [clause.strip() for clause in cost_text.split(",") if clause.strip()]


def _strip_inline_reminder_and_granted_text(line: str) -> str:
    """Keep only activated abilities printed on the source itself.

    Parenthetical reminder text and quoted abilities granted to other objects
    can contain colons, but neither is an activated ability of this card.
    Basic land types receive their mana abilities from the intrinsic type
    owner rather than from their parenthesized Oracle reminder.
    """

    preserve_token_declaration = bool(
        re.search(
            r":\s*Create .+? creature tokens? with [\"“]"
            r"This token can't (?:block|be blocked)\.",
            line,
            re.IGNORECASE,
        )
    )
    result: list[str] = []
    parenthetical_depth = 0
    quoted = False
    for character in line:
        if character in {'"', "“", "”"} and parenthetical_depth == 0:
            quoted = not quoted
            if preserve_token_declaration:
                result.append(character)
            continue
        if quoted:
            if preserve_token_declaration:
                result.append(character)
            continue
        if character == "(":
            parenthetical_depth += 1
            continue
        if character == ")" and parenthetical_depth:
            parenthetical_depth -= 1
            continue
        if parenthetical_depth == 0:
            result.append(character)
    return "".join(result).strip()


def _builtin_effect_descriptor(
    effect_text: str,
) -> tuple[str | None, FrozenMap | None]:
    normalized = " ".join(effect_text.casefold().strip(" .").split())
    normalized = re.sub(
        r"\.?\s*activate only as a sorcery$", "", normalized
    ).strip(" .")
    if normalized == "target creature you control explores":
        return (
            "builtin:explore-target",
            FrozenMap(
                {
                    "zones": ["battlefield"],
                    "categories": ["permanent"],
                    "controller": "you",
                    "creature": True,
                    "count": 1,
                }
            ),
        )
    gain = re.fullmatch(r"you gain (?P<amount>\d+) life", normalized)
    if gain is not None:
        return f"builtin:gain-life:{int(gain.group('amount'))}", None
    return None, None


def _library_search_types(effect_text: str) -> tuple[str, ...]:
    """Compile the closed ordinary fetch-land search descriptor once."""

    match = _LIBRARY_LAND_SEARCH.search(effect_text)
    if match is None:
        return ()
    values = tuple(
        part.strip()
        for part in re.split(r"\s+or\s+", match.group("types").casefold())
    )
    if not values or any(
        value not in _SUPPORTED_LIBRARY_LAND_SEARCH_TYPES
        for value in values
    ):
        return ()
    return values


def _equip_effect_descriptor() -> tuple[str, FrozenMap]:
    """Return the closed CR 702.6 target/effect contract for Equip.

    The keyword normalizer has already proved that this is a printed Equip
    ability with an ordinary mana cost. Keeping the target contract on the
    parsed ability lets proposal validation, projection, and replay use the
    same generic activation path as other activated abilities.
    """

    return (
        "builtin:equip",
        FrozenMap(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "controller": "you",
                "creature": True,
                "count": 1,
            }
        ),
    )


def _legacy_crew_keyword_ability(
    line: str,
    line_index: int,
) -> ActivatedAbility | None:
    """Interpret historical Game Record v3 Crew text.

    Current source-pinned programs use ``OrdinaryCrewAbilitySpec`` and mask
    their owned Oracle line before this compatibility parser runs.
    """

    match = re.fullmatch(r"Crew\s+(?P<power>\d+)", line, re.IGNORECASE)
    if match is None:
        return None
    threshold = int(match.group("power"))
    return ActivatedAbility(
        ability_id="crew",
        line_index=line_index,
        oracle_line=line,
        cost_text=f"Crew {threshold}",
        effect_text=(
            "This Vehicle becomes an artifact creature until end of turn. "
            f"Crew {threshold}."
        ),
        zones=("battlefield",),
        mana=normalize_mana_bundle(None),
        crew_threshold=threshold,
    )


def _craft_keyword_abilities(
    line: str,
    line_index: int,
) -> tuple[ActivatedAbility, ...]:
    match = re.fullmatch(
        r"Craft with (?P<kind>[A-Za-z]+) (?P<mana>(?:\{[^{}]+\})+) "
        r"\((?P<reminder>.+)\)",
        line,
        re.IGNORECASE,
    )
    if match is None or ":" not in match.group("reminder"):
        return ()
    kind = match.group("kind").casefold()
    cost_text, effect_text = match.group("reminder").split(":", 1)
    choice_match = re.search(
        rf"Exile an? {re.escape(kind)} you control or an? {re.escape(kind)} "
        r"card from your graveyard",
        cost_text,
        re.IGNORECASE,
    )
    if (
        choice_match is None
        or "exile this artifact" not in cost_text.casefold()
    ):
        return ()
    requirements, complex_symbols = mana_cost_to_vector(match.group("mana"))
    if complex_symbols:
        return ()
    effect = effect_text.strip()
    return tuple(
        ActivatedAbility(
            ability_id=f"craft_{zone}",
            line_index=line_index * 2 + offset,
            oracle_line=line,
            cost_text=cost_text.strip(),
            effect_text=effect,
            zones=("battlefield",),
            mana=requirements,
            exile_source=True,
            choices=(
                CostChoice(
                    kind="exile",
                    count=1,
                    zone=zone,
                    card_type=kind,
                ),
            ),
            sorcery_speed=bool(_ACTIVATE_ONLY_SORCERY.search(effect)),
        )
        for offset, zone in enumerate(("battlefield", "graveyard"))
    )


@dataclass(frozen=True, slots=True)
class _ParsedCost:
    mana: Mapping[str, int]
    complex_symbols: tuple[str, ...]
    tap_source: bool
    untap_source: bool
    discard_source: bool
    sacrifice_source: bool
    exile_source: bool
    life_payment: int
    energy_payment: int
    loyalty_delta: int | None
    choices: tuple[CostChoice, ...]
    uncompiled: tuple[str, ...]


def _parse_cost(actual_cost: str, card_name: str) -> _ParsedCost:
    requirements, raw_complex = mana_cost_to_vector(actual_cost)
    complex_symbols = [value for value in raw_complex if value not in {"T", "Q"}]
    flags = {"discard": False, "sacrifice": False, "exile": False}
    life_payment = 0
    energy_payment = 0
    loyalty_delta: int | None = None
    loyalty_clauses = 0
    choices: list[CostChoice] = []
    uncompiled: list[str] = []
    source_reference = SourceReferenceSpec(card_name)
    source_costs = {
        "discard": {"discard this card"},
        "sacrifice": {
            "sacrifice this permanent", "sacrifice this artifact",
            "sacrifice this battle", "sacrifice this creature",
            "sacrifice this enchantment", "sacrifice this equipment",
            "sacrifice this aura", "sacrifice this land",
            "sacrifice this planeswalker", "sacrifice this token",
            "sacrifice this card",
        },
        "exile": {
            "exile this card from your graveyard", "exile this card",
            "exile this permanent", "exile this artifact",
            "exile this battle", "exile this creature",
            "exile this enchantment", "exile this land",
            "exile this planeswalker", "exile this token",
        },
    }
    for clause in _split_cost_clauses(actual_cost):
        symbols = parse_mana_symbols(clause)
        residue = re.sub(r"\{[^{}]+\}", "", clause).strip()
        if not residue:
            continue
        lower = residue.casefold().strip(" .")
        loyalty_match = re.fullmatch(r"(?P<sign>[+\-\u2212])(?P<amount>\d+)", residue)
        if loyalty_match:
            amount = int(loyalty_match.group("amount"))
            loyalty_clauses += 1
            loyalty_delta = amount if loyalty_match.group("sign") == "+" else -amount
            if loyalty_clauses > 1:
                loyalty_delta = None
                if loyalty_clauses == 2:
                    uncompiled.append(
                        "multiple loyalty-symbol costs require combined-cost semantics"
                    )
            continue
        if lower.startswith("channel"):
            continue
        matched_source = next(
            (kind for kind, values in source_costs.items() if lower in values), None
        )
        if matched_source is None:
            named_source = re.fullmatch(
                r"(?P<kind>discard|sacrifice|exile) (?P<name>.+)",
                residue,
                re.IGNORECASE,
            )
            if named_source is not None and source_reference.matches(
                named_source.group("name").strip(" .")
            ):
                matched_source = named_source.group("kind").casefold()
        if matched_source is not None:
            flags[matched_source] = True
            continue
        life_match = _PAY_LIFE.match(lower)
        if life_match:
            life_payment += int(life_match.group(1))
            continue
        energy_match = _PAY_ENERGY.match(lower)
        if energy_match and "E" in symbols:
            energy_payment += _number(energy_match.group("count"))
            complex_symbols = [value for value in complex_symbols if value != "E"]
            continue
        choice = _cost_choice(lower)
        if choice is not None:
            choices.append(choice)
            continue
        uncompiled.append(residue)
    return _ParsedCost(
        mana=requirements,
        complex_symbols=tuple(complex_symbols),
        tap_source="{T}" in actual_cost.upper(),
        untap_source="{Q}" in actual_cost.upper(),
        discard_source=flags["discard"],
        sacrifice_source=flags["sacrifice"],
        exile_source=flags["exile"],
        life_payment=life_payment,
        energy_payment=energy_payment,
        loyalty_delta=loyalty_delta,
        choices=tuple(choices),
        uncompiled=tuple(uncompiled),
    )


def _cost_choice(lower: str) -> CostChoice | None:
    sacrifice = _SACRIFICE_CHOICE.match(lower)
    if sacrifice:
        return CostChoice(
            kind="sacrifice",
            count=_number(sacrifice.group("count") or "one"),
            zone="battlefield",
            card_type=sacrifice.group("kind").casefold(),
            another=bool(sacrifice.group("another")),
        )
    discard = _DISCARD_CHOICE.match(lower)
    if discard:
        return CostChoice(
            kind="discard",
            count=_number(discard.group("count")),
            zone="hand",
            card_type=discard.group("kind") or None,
        )
    returned = _RETURN_CHOICE.match(lower)
    if returned:
        return CostChoice(
            kind="return",
            zone="battlefield",
            card_type=returned.group("kind").casefold(),
        )
    return None


def _normalized_ability_line(raw_line: str) -> tuple[str, str | None]:
    line = raw_line.strip()
    line = _strip_inline_reminder_and_granted_text(line)
    for keyword, effect in (
        ("Cycling", "Draw a card."),
        (
            "Equip",
            "Attach this Equipment to target creature you control. "
            "Activate only as a sorcery.",
        ),
    ):
        match = re.fullmatch(
            rf"{keyword}\s+(?P<cost>(?:\{{[^{{}}]+\}})+)",
            line,
            re.IGNORECASE,
        )
        if match:
            discard = ", Discard this card" if keyword == "Cycling" else ""
            return f"{match.group('cost')}{discard}: {effect}", keyword
    return line, None


def _activation_conditions(effect_text: str) -> tuple[ActivationCondition, ...]:
    lower = " ".join(effect_text.casefold().split())
    marker = "activate only "
    if marker in lower:
        restriction = lower.rsplit(marker, 1)[1]
        represented = activation_restriction_spec(restriction)
        if represented is not None:
            return represented.conditions
    result: list[ActivationCondition] = []
    if "activate only during your turn" in lower:
        result.append(ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN))
    if "activate only if it's not your turn" in lower:
        result.append(
            ActivationCondition(ActivationConditionKind.NOT_CONTROLLERS_TURN)
        )
    if "activate only if you created a token this turn" in lower:
        result.append(
            ActivationCondition(ActivationConditionKind.TOKEN_CREATED_THIS_TURN)
        )
    if (
        "activate only if there are four or more card types among "
        "cards in your graveyard"
    ) in lower:
        result.append(
            ActivationCondition(
                ActivationConditionKind.GRAVEYARD_DISTINCT_TYPES,
                minimum=4,
            )
        )
    controlled = re.search(
        r"activate only if you control "
        r"(?:(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
        r"or more |an? )?(?P<kind>artifacts?|creatures?|lands?)(?:\.|$)",
        lower,
    )
    if controlled is not None:
        raw_count = controlled.group("count") or "one"
        result.append(
            ActivationCondition(
                ActivationConditionKind.CONTROLS_TYPE,
                minimum=(
                    int(raw_count)
                    if raw_count.isdigit()
                    else _NUMBER_WORDS[raw_count]
                ),
                card_type=controlled.group("kind").removesuffix("s"),
            )
        )
    recognized_if = any(
        condition.kind
        in {
            ActivationConditionKind.NOT_CONTROLLERS_TURN,
            ActivationConditionKind.TOKEN_CREATED_THIS_TURN,
            ActivationConditionKind.CONTROLS_TYPE,
            ActivationConditionKind.GRAVEYARD_DISTINCT_TYPES,
        }
        for condition in result
    )
    if "activate only if" in lower and not recognized_if:
        result.append(ActivationCondition(ActivationConditionKind.UNSUPPORTED))
    return tuple(result)


def _dynamic_mana_output(effect_text: str) -> str | None:
    lower = " ".join(effect_text.casefold().split())
    if (
        "add one mana of any color that a land an opponent controls "
        "could produce"
    ) in lower:
        return "opponent_land_colors"
    return None


def _mana_spend_restriction(effect_text: str) -> str | None:
    lower = " ".join(effect_text.casefold().split())
    if (
        "spend this mana only to cast artifact spells or activate "
        "abilities of artifacts"
    ) in lower:
        return "artifact_spell_or_ability"
    if "spend this mana only to cast an artifact spell" in lower:
        return "artifact_spell_only"
    if "spend this mana only to cast a creature spell" in lower:
        return "creature_spell_only"
    if (
        "this mana can't be spent to cast nonartifact spells" in lower
        or "this mana can't be spent to cast a nonartifact spell" in lower
    ):
        return "nonartifact_spell_prohibited"
    if (
        "spend this mana only to cast a legendary spell" in lower
        and "that spell can't be countered" in lower
    ):
        return "legendary_spell_uncounterable"
    return None


def _may_move_card_to_or_from_library(
    cost_text: str,
    effect_text: str,
) -> bool:
    """Conservatively reject CR 605.1a library-movement candidates.

    The generic activated-ability parser does not prove arbitrary movement
    prose.  A direct library reference or a keyword/action that can remove a
    card from a library therefore makes the ability nonmana until a narrower
    typed compiler proves otherwise.
    """

    normalized = " ".join(f"{cost_text} {effect_text}".casefold().split())
    return "library" in normalized or bool(
        _LIBRARY_MOVEMENT_SIGNAL.search(normalized)
    )


def _parse_activated_line(
    raw_line: str,
    line_index: int,
    card_name: str,
    keywords: Sequence[str],
) -> tuple[ActivatedAbility, ...]:
    raw = raw_line.strip()
    crew = _legacy_crew_keyword_ability(raw, line_index)
    if crew is not None:
        return (crew,)
    craft = _craft_keyword_abilities(raw, line_index)
    if craft:
        return craft
    line, keyword_override = _normalized_ability_line(raw)
    if not line or ":" not in line:
        return ()
    left, effect_text = line.split(":", 1)
    effect_text = effect_text.strip()
    if not effect_text:
        return ()
    actual_cost, keyword_prefix = _strip_keyword_prefix(left.strip())
    cost = _parse_cost(actual_cost, card_name)
    prefix = (keyword_override or keyword_prefix or "").casefold()
    if prefix == "exhaust":
        effect_text = re.sub(
            r"\s*\(activate each exhaust ability only once\.\)\s*$",
            "",
            effect_text,
            flags=re.IGNORECASE,
        ).strip()
    effect_lower = effect_text.casefold()
    mana_ability = bool(
        cost.loyalty_delta is None
        and "target" not in effect_lower
        and (effect_lower.startswith("add ") or "add one mana" in effect_lower)
        and not _may_move_card_to_or_from_library(actual_cost, effect_text)
    )
    builtin_semantic_key, target_schema = _builtin_effect_descriptor(effect_text)
    if (keyword_override or keyword_prefix or "").casefold() == "equip":
        builtin_semantic_key, target_schema = _equip_effect_descriptor()
    activation_limit = (
        ActivationLimit.EXHAUST_ONCE
        if prefix == "exhaust"
        else (
            ActivationLimit.ONCE_PER_TURN
            if "only once each turn" in effect_lower
            else None
        )
    )
    return (
        ActivatedAbility(
            ability_id=f"ab{line_index + 1}",
            line_index=line_index,
            oracle_line=line,
            cost_text=actual_cost,
            effect_text=effect_text,
            zones=_ability_zones(
                line=line,
                cost_text=actual_cost,
                effect_text=effect_text,
                keyword_prefix=keyword_override or keyword_prefix,
                keywords=keywords,
            ),
            mana=cost.mana,
            complex_symbols=cost.complex_symbols,
            tap_source=cost.tap_source,
            untap_source=cost.untap_source,
            discard_source=cost.discard_source,
            sacrifice_source=cost.sacrifice_source,
            exile_source=cost.exile_source,
            life_payment=cost.life_payment,
            energy_payment=cost.energy_payment,
            loyalty_delta=cost.loyalty_delta,
            choices=cost.choices,
            uncompiled_costs=cost.uncompiled,
            mana_ability=mana_ability,
            sorcery_speed=bool(_ACTIVATE_ONLY_SORCERY.search(effect_text)),
            generic_reduction_per_legendary_creature=int(
                bool(
                    re.search(
                        r"this ability costs \{1\} less to activate for each legendary creature you control",
                        effect_text,
                        re.IGNORECASE,
                    )
                )
            ),
            builtin_semantic_key=builtin_semantic_key,
            target_schema=target_schema,
            activation_limit=activation_limit,
            library_search_types=_library_search_types(effect_text),
            activation_conditions=_activation_conditions(effect_text),
            dynamic_mana_output=_dynamic_mana_output(effect_text),
            mana_spend_restriction=_mana_spend_restriction(effect_text),
        ),
    )


def parse_activated_abilities(
    *,
    card_name: str,
    oracle_text: str,
    keywords: Sequence[str] = (),
) -> tuple[ActivatedAbility, ...]:
    abilities: list[ActivatedAbility] = []
    for line_index, raw_line in enumerate(oracle_text.splitlines()):
        abilities.extend(
            _parse_activated_line(raw_line, line_index, card_name, keywords)
        )
    return tuple(abilities)


def choose_ability(
    abilities: Sequence[ActivatedAbility],
    selector: Any,
) -> ActivatedAbility:
    if not abilities:
        raise ValueError("No explicit activated ability was found")
    if selector is None or selector == "":
        if len(abilities) == 1:
            return abilities[0]
        raise ValueError("Select an ability by its ability id or line index")
    text = str(selector).casefold().strip()
    for ability in abilities:
        if text in {
            ability.ability_id.casefold(),
            str(ability.line_index),
            str(ability.line_index + 1),
        }:
            return ability
    raise ValueError(f"Unknown activated ability selector {selector!r}")


def reduced_requirements(
    ability: ActivatedAbility,
    *,
    legendary_creatures: int = 0,
) -> dict[str, int]:
    result = {"GENERIC": int(ability.mana.get("GENERIC", 0))}
    for color in "WUBRGC":
        result[color] = int(ability.mana.get(color, 0))
    if ability.generic_reduction_per_legendary_creature:
        reduction = ability.generic_reduction_per_legendary_creature * max(0, legendary_creatures)
        result["GENERIC"] = max(0, result["GENERIC"] - reduction)
    return result
