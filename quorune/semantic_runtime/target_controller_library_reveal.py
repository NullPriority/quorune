"""Commit the exact publicly revealed prefix through normal library entry."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..rules.target_controller_library_reveal import PARTITION_OPERATION, TARGET_CONTROLLER_REVEAL_CAPABILITY
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan, LibrarySelectionIntent

_FIELDS = {"op", "schema_version", "player", "spell_controller", "stack_ref", "looked", "selected"}


@dataclass(frozen=True, slots=True)
class PlaceMatchingRevealedPermanentHandler:
    handler_id: str = "generic.library.place-destroy-target-controller-prefix.v1"
    schema_version: int = 1
    family: str = "library.reveal"
    operation: str = PARTITION_OPERATION
    rule_references: tuple[str, ...] = ("303.4f", "303.4g", "401.4", "608.2c", "609.3", "701.20a")
    capability_dependencies: tuple[str, ...] = (TARGET_CONTROLLER_REVEAL_CAPABILITY, "library.select.battlefield_controller")

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        if (context.source is None or effect.get("stack_ref") != context.source.stack_ref
                or effect.get("spell_controller") != context.actor or effect.get("op") != self.operation
                or not _FIELDS <= set(effect)
                or set(effect) - _FIELDS - {"_aura_target_ref", "_replacement_selections"}
                or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or not isinstance(effect["looked"], (list, tuple)) or not effect["looked"]
                or type(effect["player"]) is not str or not effect["player"]):
            raise SemanticNodeError("Matching permanent placement requires the resolving spell's public prefix")
        try:
            player = effect["player"]
            context.query.require_active_seat(player)
            looked = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in effect["looked"])
            selected = effect["selected"]
            if selected is not None and (type(selected) is not str or selected != looked[-1].ref):
                raise ValueError("Only the last revealed card can be the stopping match")
            arrangement = LibrarySelectionArrangement(
                looked=looked, selected_refs=() if selected is None else (selected,),
                remainder_refs=tuple(row.ref for row in looked if row.ref != selected),
                remainder_destination="library_bottom", remainder_order="random",
                selected_destination="battlefield",
            )
            intent = LibrarySelectionIntent(
                actor=player, player=player, arrangement=arrangement, reason=context.default_reason,
                source_stack_ref=context.source.stack_ref, looked_are_public=True, selected_are_public=False,
                replacement_selections=tuple(effect.get("_replacement_selections", ())),
                entry_aura_target_ref=effect.get("_aura_target_ref"),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
