from __future__ import annotations

"""Closed scheduled results for an optional opening-hand reveal."""

from dataclasses import dataclass
from typing import Mapping
from .replacement.immutable import FrozenMap,thaw_value

OPENING_REVEAL_CAPABILITY='game.opening_hand.reveal_scheduled'
OPENING_REVEAL_HANDLER='permission.action.opening-hand-reveal.v1'


@dataclass(frozen=True,slots=True)
class OpeningRevealSpec:
    timing: str
    effects: tuple[FrozenMap,...]
    mechanics: tuple[str,...]

    def __post_init__(self):
        if (self.timing not in {'first_upkeep','your_first_upkeep','your_first_main'}
                or type(self.effects) is not tuple or not self.effects or any(not isinstance(e,FrozenMap) for e in self.effects)
                or type(self.mechanics) is not tuple or any(type(v) is not str or not v for v in self.mechanics)
                or len(set(self.mechanics))!=len(self.mechanics) or not self.required_capabilities()):
            raise ValueError('Opening reveal requires a closed timing and represented scheduled result')

    def required_capabilities(self):
        from .rules.node_capability_shapes import fixed_scry_node_capabilities
        from .rules.fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities
        from .rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        from .rules.actual_life_transfer import actual_life_transfer_capabilities
        from .rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
        effects=tuple(thaw_value(e) for e in self.effects)
        for resolver in (fixed_scry_node_capabilities,fixed_resolution_mana_capabilities,fixed_token_creation_node_capabilities,
                actual_life_transfer_capabilities,fixed_library_selection_node_capabilities):
            capabilities=resolver(effects=effects,target_schema=None,mechanic_ids=set(self.mechanics))
            if capabilities:return tuple(capabilities)
        return ()

    def to_dict(self):
        return {'schema_version':1,'timing':self.timing,'effects':[thaw_value(e) for e in self.effects],
            'mechanics':list(self.mechanics)}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','timing','effects','mechanics'}
                or type(value['schema_version']) is not int or value['schema_version']!=1
                or not isinstance(value['effects'],(list,tuple)) or not isinstance(value['mechanics'],(list,tuple))):
            raise ValueError('Opening reveal scheduled result fields changed')
        if any(not isinstance(e,Mapping) for e in value['effects']):raise ValueError('Opening result must contain typed instructions')
        return cls(value['timing'],tuple(FrozenMap(e) for e in value['effects']),tuple(value['mechanics']))
