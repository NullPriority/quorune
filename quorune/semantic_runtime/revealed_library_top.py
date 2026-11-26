"""Typed boundary for the resolving public top-card reveal."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..rules.one_revealed_card_placement import ENTER_REVEALED_TOP_OPERATION
from ..rules.revealed_library_top import REVEAL_TOP_CAPABILITY, REVEAL_TOP_OPERATION, RevealedLibraryTopSpec
from ..target_permanent_shuffle_reveal_model import WARP_OWNER_BINDING, bound_shuffle_reveal_owner
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan, LibrarySelectionIntent, RevealEachLibraryTopIntent


@dataclass(frozen=True, slots=True)
class RevealEachLibraryTopHandler:
    handler_id: str = "generic.library.reveal-each-top.v1"
    schema_version: int = 1
    family: str = "library.reveal"
    operation: str = REVEAL_TOP_OPERATION
    rule_references: tuple[str, ...] = ("121.2c", "608.2c", "701.20a", "712.8a")
    capability_dependencies: tuple[str, ...] = (REVEAL_TOP_CAPABILITY, "zone.draw.library_to_hand")

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        if context.source is None:
            raise SemanticNodeError("Public library reveal requires a resolving stack object")
        context.query.require_active_seat(context.actor)
        try:
            spec = RevealedLibraryTopSpec.from_effect(effect)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        intent = RevealEachLibraryTopIntent(context.actor, context.default_reason, spec)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


@dataclass(frozen=True, slots=True)
class EnterRevealedLibraryTopHandler:
    operation: str = ENTER_REVEALED_TOP_OPERATION
    handler_id: str = "generic.library.enter-revealed-top.v1"
    schema_version: int = 1
    family: str = "library.reveal"
    rule_references: tuple[str, ...] = ("303.4f", "400.7", "608.2c", "614.12")
    capability_dependencies: tuple[str, ...] = ("library.select.battlefield_controller",)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        required = {"op", "player", "revealed_card"}
        if (
            context.source is None
            or not required <= set(effect)
            or set(effect) - required - {WARP_OWNER_BINDING, "_aura_target_ref", "_replacement_selections"}
            or effect["op"] != self.operation
        ):
            raise SemanticNodeError("Revealed-card entry requires its resolving controller and reveal")
        try:
            player = context.actor
            if WARP_OWNER_BINDING in effect:
                player = bound_shuffle_reveal_owner(
                    effect, stack_ref=context.source.stack_ref, controller=context.actor,
                )
            if effect["player"] != player:
                raise ValueError("Revealed-card entry requires the bound library owner")
            context.query.require_active_seat(player)
            card = LibrarySelectionObjectIdentity.from_dict(effect["revealed_card"])
            arrangement = LibrarySelectionArrangement(
                looked=(card,),
                selected_refs=(card.ref,),
                remainder_refs=(),
                remainder_destination="library_bottom",
                remainder_order="chosen",
                selected_destination="battlefield",
            )
            intent = LibrarySelectionIntent(
                actor=player,
                player=player,
                arrangement=arrangement,
                reason=context.default_reason,
                source_stack_ref=context.source.stack_ref,
                looked_are_public=False,
                selected_are_public=False,
                replacement_selections=tuple(effect.get("_replacement_selections", ())),
                entry_aura_target_ref=effect.get("_aura_target_ref"),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
