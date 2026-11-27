"""Keep the optional look and the private shuffled-library partition in one resolution."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.library_partition import partition_refs
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..rules.opponent_library_shuffle import (
    OPPONENT_SHUFFLE_TOP_PARTITION_CAPABILITY, OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION,
    bound_opponent_shuffle_owner,
)
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..semantic_runtime.library_selection_intent import LibrarySelectionIntent
from .model import (
    AutoContinue, LibraryPartitionChoice, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _authorized_owner(effect, actor, stack_ref, query, *, extra=()):
    try:
        owner = bound_opponent_shuffle_owner(effect, actor, extra=extra)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    parent = query.stack_object(stack_ref)
    scope = None if parent is None or parent.controller != actor else parent.library_shuffle
    if scope is None or (scope["player"], scope["shuffle_count"]) != (owner, effect["shuffle_count"]):
        raise SemanticChoiceError("The private choice lost its original shuffled library")
    return owner, scope["library_size"]


def _partition_preparation(effect, context, owner):
    refs = context.query.library_refs(owner, top_first=True)[:2]
    rows = tuple(context.query.object(ref, zones=("library",)) for ref in refs)
    if any(row is None or row.owner != owner for row in rows):
        raise SemanticChoiceError("The private look lost an original library object")
    identities = tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)
    choice = LibraryPartitionChoice(
        "cards", refs, partitions=FrozenMap({
            "top": {"order": "top_to_bottom", "label": "Top of library"},
            "exile": {"order": "selection_order", "label": "Exile", "minimum": 0, "maximum": 1},
        }), legacy_destination=None,
    )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            "Exile up to one card and order the rest on top.",
            choice,
            FrozenMap({"stack": context.stack_ref, "operation": OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION,
                       "cards": [{"id": row.ref, "name": row.printed_name} for row in rows]}),
        ),
        continuation_effect=FrozenMap({**effect, "_looked": tuple(value.to_dict() for value in identities)}),
        preparation_intents=(RevealLibraryCardsIntent(
            actor=context.actor, player=owner, viewer=context.actor, refs_top_first=refs, reason=context.stack_label,
        ),),
    )


@dataclass(frozen=True, slots=True)
class OpponentShuffleTopPartitionHandler:
    operation: str = OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION
    handler_id: str = "choice.library.opponent-shuffle-top-partition.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("701.20a", "401.2", "401.4", "608.2c", "608.2d")
    capability_dependencies: tuple[str, ...] = (OPPONENT_SHUFFLE_TOP_PARTITION_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "library_owner",
                                             "shuffle_count", "_accepted", "_looked")
    private_data: tuple[str, ...] = ("the shuffled library's current top identities and retained order",)
    projected_fields: tuple[str, ...] = ("actor choice cards", "the publicly exiled card")
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "LibrarySelectionIntent")
    replay_fixture: str = "opponent-shuffle-private-top-partition"
    test_modules: tuple[str, ...] = ("tests.test_fixed_library_selection",)

    def prepare(self, effect, context):
        accepted = "_accepted" in effect
        owner, size = _authorized_owner(effect, context.actor, context.stack_ref, context.query,
                                        extra=("_accepted",) if accepted else ())
        if accepted and effect["_accepted"] is not True:
            raise SemanticChoiceError("The private look requires its accepted choice")
        if owner not in context.query.active_seats or size == 0:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue(
                "The shuffled library has no available top cards."))
        if accepted:
            return _partition_preparation(effect, context, owner)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest("Look at the top cards of the shuffled library?",
                ScalarChoice(field_name="choice", legal_values=("look", "decline")),
                FrozenMap({"stack": context.stack_ref, "operation": self.operation})),
            continuation_effect=FrozenMap(effect),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = continuation.semantic_frame.controller
        if "_looked" not in effect:
            _authorized_owner(effect, actor, continuation.stack_ref, query)
            choice = response.get("choice")
            if choice == "decline":
                return SemanticChoiceCompletion()
            if choice != "look":
                raise SemanticChoiceError("Choose whether to look at the shuffled library")
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap({**effect, "_accepted": True}),))
        owner, _ = _authorized_owner(effect, actor, continuation.stack_ref, query, extra=("_accepted", "_looked"))
        parent = query.stack_object(continuation.stack_ref)
        submitted = response.get("cards")
        if (effect["_accepted"] is not True or not isinstance(submitted, Mapping)
                or set(submitted) != {"top", "exile"}):
            raise SemanticChoiceError("Submit both private partitions for the looked-at cards")
        try:
            looked = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in effect["_looked"])
            top = partition_refs(submitted["top"], field="top")
            exile = partition_refs(submitted["exile"], field="exile")
            if not 1 <= len(looked) <= 2 or len(exile) > 1:
                raise ValueError("The looked-at cards allow at most one exile")
            arrangement = LibrarySelectionArrangement(looked, top, exile, "exile", "chosen", "library_top")
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(intents=(
            LibrarySelectionIntent(actor=actor, player=owner, arrangement=arrangement, reason=parent.label,
                source_stack_ref=continuation.stack_ref, looked_are_public=False, selected_are_public=False),
        ))
