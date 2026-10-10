from __future__ import annotations

"""Public-quantity output layered on the existing closed activation-cost model."""

from dataclasses import dataclass, replace
from typing import Any, Mapping
from .fixed_mana_abilities import FixedActivatedManaAbilitySpec, compile_fixed_activated_mana_ability, FixedManaMode
from .public_quantity_mana_model import PublicQuantityManaOutput
from .replacement.immutable import FrozenMap

PUBLIC_QUANTITY_MANA_HANDLER_ID='ability.activated.mana.public-quantity.v1'


@dataclass(frozen=True,slots=True)
class PublicQuantityActivatedManaAbilitySpec:
    costs: FixedActivatedManaAbilitySpec
    output: PublicQuantityManaOutput

    def __post_init__(self):
        if not isinstance(self.costs, FixedActivatedManaAbilitySpec) or not isinstance(self.output, PublicQuantityManaOutput):
            raise ValueError('Public quantity mana requires closed costs and output')
        if self.costs.modes != (FixedManaMode(green=1),) or self.costs.spend_restriction is not None:
            raise ValueError('Public quantity cost descriptor cannot carry a competing mana output')

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
    return PublicQuantityActivatedManaAbilitySpec(costs,output)
