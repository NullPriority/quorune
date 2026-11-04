"""A spell controller's immutable selection for one physical Mutate merge."""

from dataclasses import dataclass
from ..continuous_effect_model import ContinuousObjectIdentity
from ..permanent_composition import MergePosition,_validate_identity


@dataclass(frozen=True,slots=True)
class SelectMutateMergeIntent:
    actor:str
    stack_ref:str
    source:ContinuousObjectIdentity
    target:ContinuousObjectIdentity
    position:MergePosition

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor or self.actor.strip()!=self.actor
                or type(self.stack_ref) is not str or not self.stack_ref or self.stack_ref.strip()!=self.stack_ref
                or not isinstance(self.position,MergePosition)):
            raise ValueError('Mutate merge requires its current controller, stack reference and position')
        _validate_identity(self.source);_validate_identity(self.target)
        if self.source.object_id==self.target.object_id:
            raise ValueError('A mutating spell cannot merge with itself')
