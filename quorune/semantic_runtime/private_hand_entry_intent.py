from __future__ import annotations

"""An incarnation-pinned specialization of the existing simultaneous move intent."""

from dataclasses import dataclass

from ..private_hand_entry_model import PrivateHandCollectionSpec
from ..public_zone_moves import PublicBattlefieldController
from ..replacement.immutable import FrozenMap
from .intents import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class PrivateHandEntryIntent(MoveObjectsSimultaneouslyIntent):
    selection: PrivateHandCollectionSpec | None = None
    selected_identities: FrozenMap = FrozenMap()
    aura_target_refs: FrozenMap = FrozenMap()

    def __post_init__(self):
        super(PrivateHandEntryIntent, self).__post_init__()
        if (not isinstance(self.selection, PrivateHandCollectionSpec)
                or self.expected_zones != ("hand",) or self.destination != "battlefield"
                or not self.owned_only or self.controlled_only
                or self.battlefield_controller is not PublicBattlefieldController.ACTOR
                or self.battlefield_tapped != self.selection.tapped
                or self.selection.maximum is not None and len(self.object_refs) > self.selection.maximum):
            raise ValueError("Selected hand entry requires its complete typed batch instruction")
        for field in ("selected_identities", "aura_target_refs"):
            value = getattr(self, field)
            if not isinstance(value, FrozenMap):
                object.__setattr__(self, field, FrozenMap(value))
        if (set(self.selected_identities) != set(self.object_refs)
                or any(type(value) is not str or not value for value in self.selected_identities.values())
                or any(type(key) is not str or not key or type(value) is not str or not value
                       for key, value in self.aura_target_refs.items())):
            raise ValueError("Selected hand entry requires sealed identities and attachment choices")
