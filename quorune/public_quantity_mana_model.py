from __future__ import annotations

"""Closed public amount and color selection for mana production."""

from dataclasses import dataclass, replace
from typing import Any, Mapping
from .characteristic_fragments import CharacteristicQuantityReduction, CharacteristicQuantitySpec
from .entry_designations import ENTRY_DESIGNATION_CAPABILITY
from .object_predicate import ObjectQuerySpec
from .scalar_effect_amount_model import ScalarEffectAmountSpec, ScalarAmountOrigin, SCALAR_AMOUNT_KIND
from .mana_restrictions import valid_mana_spend_restriction
from .characteristic_fragments import CharacteristicQuantityScope
from .query_effect_amount_model import PublicQueryAmountSpec
from .live_battlefield_quantity_model import LiveBattlefieldQuantitySpec, LIVE_BATTLEFIELD_QUANTITY_KIND

PUBLIC_QUANTITY_MANA_KIND = 'public_quantity_mana'
PUBLIC_QUANTITY_MANA_CAPABILITY = 'mana.production.public_quantity'
CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION = 2
CHOSEN_SUBTYPE_QUANTITY_MANA_CAPABILITY = "mana.production.controlled_chosen_creature_type"
PAID_SACRIFICE_QUANTITY_MANA_VERSION = 3
PAID_SACRIFICE_QUANTITY_MANA_CAPABILITY = "mana.production.selected_sacrifice_mana_value"
PAID_ACTIVATION_MANA_VALUE_CONTEXT = "paid_activation_sacrifice_mana_value"
CHOSEN_DEVOTION_MANA_VERSION = 4
CHOSEN_DEVOTION_MANA_CAPABILITY = "mana.production.chosen_color_devotion"
CONTROLLED_LAND_SUBTYPE_PAIR_MANA_VERSION = 5
CONTROLLED_LAND_SUBTYPE_PAIR_MANA_CAPABILITY = "mana.production.controlled_land_subtype_pair"
CONTROLLED_CREATURE_THRESHOLD_MANA_VERSION = 6
CONTROLLED_CREATURE_THRESHOLD_MANA_CAPABILITY = "mana.production.controlled_creature_threshold"
_CREATURE_THRESHOLD_MANA_KIND = "controlled_creature_threshold_mana"
_LAND_SUBTYPE_PAIR_MANA_KIND = "controlled_land_subtype_pair_mana"
_PAIRED_LAND_SUBTYPES = frozenset({"mine", "power-plant", "tower"})


def chosen_subtype_quantity_mana_count() -> CharacteristicQuantitySpec:
    return CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
    )


def chosen_devotion_mana_count() -> CharacteristicQuantitySpec:
    return CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=("battlefield",)), schema_version=2,
        reduction=CharacteristicQuantityReduction.DEVOTION, mana_colors=("W",),
    )


@dataclass(frozen=True, slots=True)
class ControlledLandSubtypePairManaAmount:
    land_subtypes: tuple[str, str]
    enhanced_amount: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.land_subtypes, tuple) or len(self.land_subtypes) != 2
            or any(type(value) is not str or value not in _PAIRED_LAND_SUBTYPES for value in self.land_subtypes)
            or self.land_subtypes != tuple(sorted(set(self.land_subtypes)))
            or type(self.enhanced_amount) is not int or self.enhanced_amount not in (2, 3)
        ):
            raise ValueError("Land mana requires two different canonical Urza land subtypes and a printed enhanced amount")

    @property
    def queries(self) -> tuple[CharacteristicQuantitySpec, ...]:
        return tuple(
            CharacteristicQuantitySpec(
                CharacteristicQuantityScope.CONTROLLER_ZONE,
                query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",),
                                      subtypes_all=(subtype, "urza's")),
            )
            for subtype in self.land_subtypes
        )

    def to_dict(self) -> dict[str, Any]:
        return {"kind": _LAND_SUBTYPE_PAIR_MANA_KIND, "land_subtypes": list(self.land_subtypes),
                "enhanced_amount": self.enhanced_amount}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ControlledLandSubtypePairManaAmount:
        if (
            not isinstance(value, Mapping) or set(value) != {"kind", "land_subtypes", "enhanced_amount"}
            or value["kind"] != _LAND_SUBTYPE_PAIR_MANA_KIND
            or not isinstance(value["land_subtypes"], (list, tuple))
        ):
            raise ValueError("Land subtype pair mana fields are missing or unknown")
        return cls(tuple(value["land_subtypes"]), value["enhanced_amount"])


@dataclass(frozen=True, slots=True)
class ControlledCreatureThresholdManaAmount:
    minimum_creatures: int

    def __post_init__(self) -> None:
        if type(self.minimum_creatures) is not int or not 1 <= self.minimum_creatures <= 10:
            raise ValueError("Creature threshold mana requires a fixed positive creature threshold")

    @property
    def query(self) -> CharacteristicQuantitySpec:
        return chosen_subtype_quantity_mana_count()

    def to_dict(self) -> dict[str, Any]:
        return {"kind": _CREATURE_THRESHOLD_MANA_KIND, "minimum_creatures": self.minimum_creatures}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ControlledCreatureThresholdManaAmount:
        if (not isinstance(value, Mapping) or set(value) != {"kind", "minimum_creatures"}
                or value["kind"] != _CREATURE_THRESHOLD_MANA_KIND):
            raise ValueError("Creature threshold mana fields are missing or unknown")
        return cls(value["minimum_creatures"])


@dataclass(frozen=True, slots=True)
class PublicQuantityManaOutput:
    selection: str
    colors: tuple[str, ...]
    amount: (
        CharacteristicQuantitySpec | ScalarEffectAmountSpec | LiveBattlefieldQuantitySpec
        | ControlledLandSubtypePairManaAmount | ControlledCreatureThresholdManaAmount
    )
    restriction: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {
            1, CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION, PAID_SACRIFICE_QUANTITY_MANA_VERSION,
            CHOSEN_DEVOTION_MANA_VERSION, CONTROLLED_LAND_SUBTYPE_PAIR_MANA_VERSION,
            CONTROLLED_CREATURE_THRESHOLD_MANA_VERSION,
        }:
            raise ValueError('Unsupported public quantity mana version')
        if type(self.selection) is not str or self.selection not in {'fixed','choose_one','combination'}:
            raise ValueError('Unsupported public quantity mana selection')
        colors=tuple(self.colors)
        if not colors or any(type(c) is not str or c not in 'WUBRGC' or len(c)!=1 for c in colors):
            raise ValueError('Public quantity mana requires canonical colors')
        if colors!=tuple(sorted(set(colors))) or (self.selection=='fixed' and len(colors)!=1):
            raise ValueError('Public quantity mana color selection is malformed')
        if self.selection!='fixed' and 'C' in colors:
            raise ValueError('Any-color selection cannot include colorless')
        if not isinstance(self.amount, (
            CharacteristicQuantitySpec, ScalarEffectAmountSpec, LiveBattlefieldQuantitySpec,
            ControlledLandSubtypePairManaAmount, ControlledCreatureThresholdManaAmount,
        )):
            raise ValueError("Public quantity mana requires a typed producer")
        paired_subtypes = isinstance(self.amount, ControlledLandSubtypePairManaAmount)
        if paired_subtypes != (self.schema_version == CONTROLLED_LAND_SUBTYPE_PAIR_MANA_VERSION):
            raise ValueError("Land subtype pair mana requires its explicit output version")
        if paired_subtypes and (self.selection != "fixed" or colors != ("C",) or self.restriction is not None):
            raise ValueError("Land subtype pair mana has exactly one unrestricted colorless output")
        creature_threshold = isinstance(self.amount, ControlledCreatureThresholdManaAmount)
        if creature_threshold != (self.schema_version == CONTROLLED_CREATURE_THRESHOLD_MANA_VERSION):
            raise ValueError("Creature threshold mana requires its explicit output version")
        if creature_threshold and (self.selection != "fixed" or self.restriction is not None):
            raise ValueError("Creature threshold mana has exactly one unrestricted output color")
        if (isinstance(self.amount, CharacteristicQuantitySpec)
                and self.amount.scope is not CharacteristicQuantityScope.SOURCE_COUNTER
                and self.schema_version != CHOSEN_DEVOTION_MANA_VERSION):
            PublicQueryAmountSpec(replace(self.amount,exclude_source=False))
        if self.schema_version == PAID_SACRIFICE_QUANTITY_MANA_VERSION:
            if (not isinstance(self.amount, ScalarEffectAmountSpec)
                    or self.amount.origin is not ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE
                    or self.selection != "fixed" or self.restriction is not None):
                raise ValueError("Paid mana value requires one fixed output from the selected sacrifice")
        elif isinstance(self.amount,ScalarEffectAmountSpec) and self.amount.origin not in {ScalarAmountOrigin.SOURCE,ScalarAmountOrigin.HISTORY,ScalarAmountOrigin.PUBLIC_AGGREGATE}:
            raise ValueError('Target, payment or event-relative mana amounts are outside this grammar')
        if isinstance(self.amount,ScalarEffectAmountSpec) and (self.amount.coefficient != 1 or self.amount.binding_id is not None):
            raise ValueError('Public quantity mana does not carry scalar arithmetic or declaration caches')
        if isinstance(self.amount,ScalarEffectAmountSpec) and self.amount.origin is ScalarAmountOrigin.SOURCE and self.amount.characteristic not in {'power','toughness'}:
            raise ValueError('Public quantity mana source fields are power or toughness')
        if self.restriction is not None and not valid_mana_spend_restriction(self.restriction):
            raise ValueError('Public quantity mana spend restriction is unsupported')
        if self.schema_version == CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION and (
            self.selection != "choose_one" or colors != tuple(sorted("WUBRG"))
            or self.restriction is not None or self.amount != chosen_subtype_quantity_mana_count()
        ):
            raise ValueError("Chosen subtype mana requires one full ordinary color choice and its exact controlled creature count")
        if self.schema_version == CHOSEN_DEVOTION_MANA_VERSION and (
            self.selection != "choose_one" or colors != tuple(sorted("WUBRG"))
            or self.restriction is not None or self.amount != chosen_devotion_mana_count()
        ):
            raise ValueError("Devotion mana requires one ordinary color and its controlled battlefield devotion")
        object.__setattr__(self,'colors',colors)

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        if self.schema_version == CONTROLLED_CREATURE_THRESHOLD_MANA_VERSION:
            return (PUBLIC_QUANTITY_MANA_CAPABILITY, CONTROLLED_CREATURE_THRESHOLD_MANA_CAPABILITY)
        if self.schema_version == CONTROLLED_LAND_SUBTYPE_PAIR_MANA_VERSION:
            return (PUBLIC_QUANTITY_MANA_CAPABILITY, CONTROLLED_LAND_SUBTYPE_PAIR_MANA_CAPABILITY)
        if self.schema_version == PAID_SACRIFICE_QUANTITY_MANA_VERSION:
            return (PUBLIC_QUANTITY_MANA_CAPABILITY, PAID_SACRIFICE_QUANTITY_MANA_CAPABILITY)
        if self.schema_version == CHOSEN_DEVOTION_MANA_VERSION:
            return (PUBLIC_QUANTITY_MANA_CAPABILITY, CHOSEN_DEVOTION_MANA_CAPABILITY)
        if self.schema_version == CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION:
            return (PUBLIC_QUANTITY_MANA_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY,
                    CHOSEN_SUBTYPE_QUANTITY_MANA_CAPABILITY)
        return (PUBLIC_QUANTITY_MANA_CAPABILITY,)

    def to_dict(self) -> dict[str, Any]:
        return {'kind':PUBLIC_QUANTITY_MANA_KIND,'schema_version':self.schema_version,
            'selection':self.selection,'colors':list(self.colors),'amount':self.amount.to_dict(),'restriction':self.restriction}

    @classmethod
    def from_dict(cls, value: Mapping[str,Any]) -> PublicQuantityManaOutput:
        fields={'kind','schema_version','selection','colors','amount','restriction'}
        if not isinstance(value,Mapping) or set(value)!=fields or value['kind']!=PUBLIC_QUANTITY_MANA_KIND:
            raise ValueError('Public quantity mana fields are missing or unknown')
        if not isinstance(value['colors'],(list,tuple)) or not isinstance(value['amount'],Mapping):
            raise ValueError('Public quantity mana colors and amount are malformed')
        raw=value['amount']
        amount=(ControlledLandSubtypePairManaAmount.from_dict(raw) if raw.get("kind") == _LAND_SUBTYPE_PAIR_MANA_KIND
            else ControlledCreatureThresholdManaAmount.from_dict(raw) if raw.get("kind") == _CREATURE_THRESHOLD_MANA_KIND
            else ScalarEffectAmountSpec.from_dict(raw) if raw.get('kind')==SCALAR_AMOUNT_KIND
            else LiveBattlefieldQuantitySpec.from_dict(raw) if raw.get('kind')==LIVE_BATTLEFIELD_QUANTITY_KIND
            else CharacteristicQuantitySpec.from_dict(raw))
        return cls(value['selection'],tuple(value['colors']),amount,value['restriction'],value['schema_version'])


def validate_dynamic_mana_output(ability):
    from .replacement.immutable import FrozenMap
    value=ability.dynamic_mana_output
    if value is None or value=='opponent_land_colors':
        return
    from .fixed_mana_abilities import LUCK_COUNTER_MANA_OUTPUT, fixed_mana_modes_from_effect

    if value == LUCK_COUNTER_MANA_OUTPUT:
        if (
            not ability.mana_ability or not ability.tap_source or ability.zones != ("battlefield",)
            or ability.fixed_mana_outputs != fixed_mana_modes_from_effect("Add {C}.")
            or any(ability.mana.values())
            or any((ability.life_payment, ability.sacrifice_source, ability.untap_source, ability.discard_source,
                    ability.exile_source, ability.mana_cost_options, ability.color_set_mana_output,
                    ability.mana_spend_restriction, ability.fixed_mana_result, ability.activation_limit,
                    ability.activation_conditions, ability.source_counter_removal_cost, ability.choices,
                    ability.target_schema, ability.loyalty_delta is not None))
        ):
            raise ValueError("Luck-counter color choice requires its closed mana activation")
        return
    from .artifact_hand_mana_model import (
        artifact_hand_mana_output, is_artifact_hand_mana, validate_artifact_hand_mana_ability,
    )

    if is_artifact_hand_mana(ability):
        validate_artifact_hand_mana_ability(ability)
        object.__setattr__(ability, "dynamic_mana_output", FrozenMap(artifact_hand_mana_output()))
        return
    from .land_mana_potential import land_mana_potential_output, validate_land_potential_ability

    land_potential = land_mana_potential_output(ability)
    if land_potential is not None:
        validate_land_potential_ability(ability, land_potential)
        object.__setattr__(ability, "dynamic_mana_output", FrozenMap(land_potential.to_dict()))
        return

    from .land_play_mana_model import land_play_mana_output, validate_land_play_mana_ability

    output = land_play_mana_output(ability)
    if output is not None:
        validate_land_play_mana_ability(ability)
        object.__setattr__(ability, "dynamic_mana_output", FrozenMap(output.to_dict()))
        return
    from .chosen_color_mana_model import CHOSEN_COLOR_MANA_KIND, ChosenColorManaOutput
    output = (ChosenColorManaOutput.from_dict(value) if isinstance(value, Mapping) and value.get('kind') == CHOSEN_COLOR_MANA_KIND
              else PublicQuantityManaOutput.from_dict(value))
    if not ability.mana_ability or ability.fixed_mana_outputs or ability.color_set_mana_output is not None:
        raise ValueError('Public quantity mana cannot mix competing output descriptors')
    if output.restriction != ability.mana_spend_restriction or ability.loyalty_delta is not None or ability.target_schema is not None:
        raise ValueError('Public quantity mana classification or restriction is inconsistent')
    object.__setattr__(ability,'dynamic_mana_output',FrozenMap(output.to_dict()))
