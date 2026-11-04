from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from ..composition_order_model import CompositionOrderRequest
from ..replacement.immutable import FrozenMap
from .mutate_intent import SelectMutateMergeIntent


@dataclass(frozen=True,slots=True)
class ResumeCompositionOrderIntent:
    request: CompositionOrderRequest
    original_effect: FrozenMap
    semantic_frame: FrozenMap
    ordered_refs: tuple[str,...]

    def __post_init__(self):
        if (not isinstance(self.request,CompositionOrderRequest) or not isinstance(self.original_effect,Mapping)
                or not isinstance(self.semantic_frame,Mapping)):
            raise ValueError('Composition completion requires its typed request and parent instruction')
        self.request.validate_order(self.ordered_refs)
        object.__setattr__(self,'original_effect',FrozenMap(self.original_effect))
        object.__setattr__(self,'semantic_frame',FrozenMap(self.semantic_frame))
        object.__setattr__(self,'ordered_refs',tuple(self.ordered_refs))

    @property
    def actor(self):
        return self.request.chooser


CompositionIntent = ResumeCompositionOrderIntent | SelectMutateMergeIntent
