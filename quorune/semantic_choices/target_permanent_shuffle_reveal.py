"""Reveal the exact top after the target owner finishes shuffling."""

from dataclasses import dataclass

from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..replacement.immutable import FrozenMap
from ..rules.one_revealed_card_placement import ENTER_REVEALED_TOP_OPERATION
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..target_permanent_shuffle_reveal_model import (
    WARP_CAPABILITY,
    WARP_OPERATION,
    WARP_OWNER_BINDING,
    TargetPermanentShuffleRevealSpec,
)
from .fixed_library_selection import _current_library_top, _identity
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


@dataclass(frozen=True, slots=True)
class TargetPermanentShuffleRevealHandler:
    operation: str = WARP_OPERATION
    handler_id: str = "choice.library.target-permanent-shuffle-reveal.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 110.2a", "CR 110.4a", "CR 111.8", "CR 303.4f", "CR 400.7",
        "CR 608.2b", "CR 608.2c", "CR 701.20a", "CR 701.24c", "CR 701.24e",
    )
    capability_dependencies: tuple[str, ...] = (WARP_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player")
    private_data: tuple[str, ...] = ("the target owner's exact post-shuffle top before public reveal",)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "LibrarySelectionIntent")
    replay_fixture: str = "target-owner-shuffle-and-public-top"
    test_modules: tuple[str, ...] = ("tests.test_target_owner_shuffle_top",)

    def prepare(self, effect, context):
        try:
            owner = TargetPermanentShuffleRevealSpec.from_effect(effect, resolved=True).player
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc

        stack = context.query.stack_object(context.stack_ref)
        if (
            stack is None or len(stack.targets) != 1 or context.actor != owner
            or stack.target_snapshots.get(stack.targets[0], {}).get("owner") != owner
        ):
            raise SemanticChoiceError("The shuffled library belongs to the announced permanent's owner")
        if owner not in context.query.active_seats:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), auto_continue=AutoContinue("The owner left the game."),
            )
        top = _current_library_top(context.query, owner)
        if top is None:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), auto_continue=AutoContinue("The owner's library is empty."),
            )

        entry = ()
        if top.physical_card and PERMANENT_CARD_TYPES.intersection(top.types):
            entry = (FrozenMap({
                "op": ENTER_REVEALED_TOP_OPERATION,
                "player": owner,
                "revealed_card": _identity(top).to_dict(),
                WARP_OWNER_BINDING: {
                    "schema_version": 1,
                    "stack_ref": context.stack_ref,
                    "controller": context.stack_controller,
                    "owner": owner,
                },
            }),)
        return SemanticChoicePreparation(
            None,
            FrozenMap(effect),
            preparation_intents=(
                RevealLibraryCardsIntent(
                    actor=owner, player=owner, viewer=owner, refs_top_first=(top.ref,),
                    reason=context.stack_label, public=True, announce_public_top=True,
                ),
            ),
            auto_continue=AutoContinue("Apply the printed result to the revealed top.", prepend_effects=entry),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("The target-owner reveal has no direct choice response")
