from __future__ import annotations

"""Public-quantity output layered on the existing closed activation-cost model."""

from dataclasses import dataclass, replace
from typing import Any, Mapping
from .fixed_mana_abilities import FixedActivatedManaAbilitySpec, compile_fixed_activated_mana_ability, FixedManaMode
from .public_quantity_mana_model import (
    CHOSEN_DEVOTION_MANA_VERSION, CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION,
    CONTROLLED_CREATURE_THRESHOLD_MANA_VERSION, CONTROLLED_LAND_SUBTYPE_PAIR_MANA_VERSION,
    PAID_SACRIFICE_QUANTITY_MANA_VERSION, PublicQuantityManaOutput,
)
from .replacement.immutable import FrozenMap
from .sacrifice_union_model import COLORED_OR_ARTIFACT_CREATURE_UNION_VERSION, controlled_permanent_predicate

PUBLIC_QUANTITY_MANA_HANDLER_ID='ability.activated.mana.public-quantity.v1'


def _generic_two_tap_mana_price(costs: FixedActivatedManaAbilitySpec) -> bool:
    return (costs.tap_source and {key: value for key, value in costs.mana_cost.items() if value} == {"GENERIC": 2}
            and not (costs.sacrifice_source or costs.life_payment or costs.activation_limit is not None
                     or costs.activation_conditions or costs.source_counter_removal_cost is not None
                     or costs.result is not None or costs.choices or costs.mana_cost_options))


def _ordinary_source_tap_mana_price(costs: FixedActivatedManaAbilitySpec) -> bool:
    return (
        costs.tap_source and not any(costs.mana_cost.values())
        and not (costs.sacrifice_source or costs.life_payment or costs.activation_limit is not None
                 or costs.activation_conditions or costs.source_counter_removal_cost is not None
                 or costs.result is not None or costs.choices or costs.mana_cost_options
                 or costs.luck_counter_color_choice)
    )


def paid_sacrifice_mana_price_type(choices) -> str | None:
    if len(choices) != 1:
        return None
    choice = choices[0]
    cost = choice.fixed_zone_change_cost()
    if cost is None or cost.operation != "sacrifice_one" or cost.count != 1:
        return None
    if cost.predicate_union is not None:
        return "creature" if cost.predicate_union.schema_version == COLORED_OR_ARTIFACT_CREATURE_UNION_VERSION else None
    for kind in ("creature", "artifact"):
        if cost.predicate in (controlled_permanent_predicate(types_all=(kind,)),
                              controlled_permanent_predicate(types_any=(kind,))):
            return kind
    return None


def _paid_sacrifice_mana_price(costs: FixedActivatedManaAbilitySpec) -> bool:
    return (costs.tap_source and not any(costs.mana_cost.values())
            and not (costs.sacrifice_source or costs.life_payment or costs.activation_limit is not None
                     or costs.activation_conditions or costs.source_counter_removal_cost is not None
                     or costs.result is not None or costs.mana_cost_options)
            and paid_sacrifice_mana_price_type(costs.choices) is not None)


@dataclass(frozen=True,slots=True)
class PublicQuantityActivatedManaAbilitySpec:
    costs: FixedActivatedManaAbilitySpec
    output: PublicQuantityManaOutput

    def __post_init__(self):
        if not isinstance(self.costs, FixedActivatedManaAbilitySpec) or not isinstance(self.output, PublicQuantityManaOutput):
            raise ValueError('Public quantity mana requires closed costs and output')
        if self.costs.modes != (FixedManaMode(green=1),) or self.costs.spend_restriction is not None:
            raise ValueError('Public quantity cost descriptor cannot carry a competing mana output')
        if self.output.schema_version in {CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION, CHOSEN_DEVOTION_MANA_VERSION} and not _generic_two_tap_mana_price(self.costs):
            raise ValueError("The chosen quantity requires the exact generic-two tap activation")
        if self.output.schema_version == PAID_SACRIFICE_QUANTITY_MANA_VERSION and not _paid_sacrifice_mana_price(self.costs):
            raise ValueError("Paid mana value requires a tap and one exact selected sacrifice")
        if self.output.schema_version in {CONTROLLED_LAND_SUBTYPE_PAIR_MANA_VERSION, CONTROLLED_CREATURE_THRESHOLD_MANA_VERSION}:
            if not _ordinary_source_tap_mana_price(self.costs):
                raise ValueError("Conditional fixed mana requires only the ordinary source tap")

    def to_dict(self) -> dict[str,Any]:
        return {'costs':self.costs.to_dict(),'output':self.output.to_dict()}

    @classmethod
    def from_dict(cls,value: Mapping[str,Any]):
        if not isinstance(value,Mapping) or set(value)!={'costs','output'}:
            raise ValueError('Public quantity mana ability fields are missing or unknown')
        return cls(FixedActivatedManaAbilitySpec.from_dict(value['costs']),PublicQuantityManaOutput.from_dict(value['output']))

    def to_activated_ability(self):
        return replace(self.costs.to_activated_ability(),fixed_mana_outputs=(),
            dynamic_mana_output=FrozenMap(self.output.to_dict()),mana_spend_restriction=self.output.restriction)


def public_quantity_mana_handler_descriptor(spec):
    return {'handler_id':PUBLIC_QUANTITY_MANA_HANDLER_ID,'schema_version':1,'event':'activate','ability':spec.to_dict()}


def compile_public_quantity_activated_mana_ability(ability,output):
    costs = compile_mana_activation_costs(ability)
    if costs is None or (output.schema_version in {CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION, CHOSEN_DEVOTION_MANA_VERSION}
                         and not _generic_two_tap_mana_price(costs)):
        return None
    if output.schema_version == PAID_SACRIFICE_QUANTITY_MANA_VERSION and not _paid_sacrifice_mana_price(costs):
        return None
    if (output.schema_version in {CONTROLLED_LAND_SUBTYPE_PAIR_MANA_VERSION, CONTROLLED_CREATURE_THRESHOLD_MANA_VERSION}
            and not _ordinary_source_tap_mana_price(costs)):
        return None
    return PublicQuantityActivatedManaAbilitySpec(costs, output)


def compile_mana_activation_costs(ability):
    """Use the fixed-family cost boundary without claiming its placeholder output."""
    # This internal fixed output establishes only the already represented cost
    # and activation constraints. The enclosing descriptor owns actual output.
    if ability.loyalty_delta is not None or ability.target_schema is not None:
        return None
    placeholder=replace(ability,effect_text='Add {G}.',mana_ability=True,
        mana_spend_restriction=None,fixed_mana_outputs=(FixedManaMode(green=1),))
    costs=compile_fixed_activated_mana_ability(placeholder)
    if costs is None:
        return None
    costs=replace(costs,effect_text=ability.effect_text)
    return costs
