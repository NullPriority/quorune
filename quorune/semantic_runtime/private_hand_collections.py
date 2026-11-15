from __future__ import annotations

"""Resume a selected hand batch through normal replacement and Aura suspension."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..private_hand_entry_model import (
    PRIVATE_HAND_COLLECTION_CAPABILITY, SELECTED_HAND_ENTRY_OPERATION, PrivateHandCollectionSpec,
)
from ..public_zone_moves import PublicBattlefieldController
from ..replacement.immutable import FrozenMap
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan
from .private_hand_entry_intent import PrivateHandEntryIntent


@dataclass(frozen=True, slots=True)
class SelectedHandEntryHandler:
    operation: str = SELECTED_HAND_ENTRY_OPERATION
    handler_id: str = "generic.selected-hand-entry.v1"
    schema_version: int = 1
    family: str = "effect.private-hand-collection"
    rule_references: tuple[str, ...] = ("303.4f", "303.4g", "400.7", "603.6a", "608.2c", "614.12")
    capability_dependencies: tuple[str, ...] = (PRIVATE_HAND_COLLECTION_CAPABILITY,)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        required = {"op", "player", "selection", "selected"}
        if (not required <= set(effect)
                or set(effect) - required - {"_replacement_selections", "_aura_target_refs"}
                or effect["op"] != self.operation or effect["player"] != context.actor
                or not isinstance(effect["selected"], Mapping) or not effect["selected"]):
            raise SemanticNodeError("Selected hand-entry effect is malformed")
        try:
            spec = PrivateHandCollectionSpec.from_dict(effect["selection"])
            intent = PrivateHandEntryIntent(
                actor=context.actor, object_refs=tuple(sorted(effect["selected"])),
                expected_zones=("hand",), destination="battlefield", reason=context.default_reason,
                owned_only=True, battlefield_controller=PublicBattlefieldController.ACTOR,
                battlefield_tapped=spec.tapped, selection=spec,
                selected_identities=FrozenMap(effect["selected"]),
                aura_target_refs=FrozenMap(effect.get("_aura_target_refs", {})),
                replacement_selections=tuple(effect.get("_replacement_selections", ())),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
