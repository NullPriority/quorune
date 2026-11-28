"""Read a single library incarnation and commit its public reveal."""

from dataclasses import dataclass

from ..card_program_faces import prospective_face_view
from ..carddb_characteristics import library_card_rules_names
from ..replacement.immutable import FrozenMap
from ..rules.revealed_creature_statistics import (
    REVEALED_CREATURE_DEPENDENCIES, REVEALED_CREATURE_OPERATION,
    revealed_creature_dependencies, revealed_creature_followups,
)
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from .fixed_library_selection import _current_library_top
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


def revealed_creature_top_id(host, actor, effect):
    if effect.get("op") != REVEALED_CREATURE_OPERATION:
        return None
    library = host.state.players[actor].zones["library"]
    return library[-1] if library else None


def revealed_top_effective_data(host, card):
    record = host.card_record(card)
    if record is None or not record.faces:
        return host._effective_card_data(card)
    names = library_card_rules_names(record)
    if len(names) != 1:
        return host._effective_card_data(card)
    return host._effective_card_data(prospective_face_view(record, card, names[0]))


@dataclass(frozen=True, slots=True)
class RevealedCreatureStatisticsHandler:
    operation: str = REVEALED_CREATURE_OPERATION
    handler_id: str = "choice.library.reveal-controller-creature-statistics.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 107.1b", "CR 121.2", "CR 208.2a", "CR 608.2c", "CR 608.2h", "CR 701.20a", "CR 701.22a",
    )
    capability_dependencies: tuple[str, ...] = REVEALED_CREATURE_DEPENDENCIES
    continuation_fields: tuple[str, ...] = ("player", "schema_version")
    private_data: tuple[str, ...] = ("the controller's top card before its public reveal",)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "DrawCardsIntent", "LifeChangeIntent")
    replay_fixture: str = "scry-public-revealed-creature-statistics"
    test_modules: tuple[str, ...] = ("tests.test_revealed_creature_statistics",)

    def prepare(self, effect, context):
        actor = context.actor
        if actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("The public reveal requires the resolving controller")
        try:
            revealed_creature_dependencies(effect, player=actor)
            top = _current_library_top(context.query, actor)
            if top is None:
                return SemanticChoicePreparation(
                    None, FrozenMap(effect), auto_continue=AutoContinue("There is no library top to reveal."),
                )
            followups = revealed_creature_followups(top, player=actor)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            preparation_intents=(RevealLibraryCardsIntent(
                actor, actor, actor, (top.ref,), context.stack_label, public=True, announce_public_top=True,
            ),),
            auto_continue=AutoContinue("Apply the revealed creature's statistics.", prepend_effects=followups),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("A revealed creature result never issues a player choice")
