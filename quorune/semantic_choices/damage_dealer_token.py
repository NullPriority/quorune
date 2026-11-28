"""The original combat damage dealer's controller chooses about their token."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.subtype_damage_controller import CAPABILITY, CHOICE_OPERATION, subtype_damage_dependencies
from .model import (AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)


def _validate(effect, player):
    try:
        subtype_damage_dependencies(effect, player=player)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class DamageDealerTokenChoiceHandler:
    operation: str = CHOICE_OPERATION
    handler_id: str = "choice.token.subtype-damage-dealer-controller.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("111.2", "603.3a", "608.2d", "608.2h")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "committed_damage_dealer_subtype", "token")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("SemanticChoiceCompletion.prepend_effects", "create_token")
    replay_fixture: str = "subtype-damage-dealer-token-choice"
    test_modules: tuple[str, ...] = ("tests.test_sliver_damage_results",)

    def prepare(self, effect, context):
        _validate(effect, context.actor)
        frozen = FrozenMap(effect)
        if context.actor not in context.query.active_seats:
            return SemanticChoicePreparation(None, frozen, auto_continue=AutoContinue("The player left the game."))
        return SemanticChoicePreparation(
            SemanticChoiceRequest(f"Create a {effect['token']['name']}?",
                ScalarChoice("choice", ("create", "decline")), FrozenMap({"stack": context.stack_ref})),
            frozen,
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        player = effect.get("player")
        _validate(effect, player)
        choice = response.get("choice")
        if player not in query.active_seats or choice not in ("create", "decline"):
            raise SemanticChoiceError("Choose create or decline for the original damage dealer")
        return SemanticChoiceCompletion(prepend_effects=(effect["token"],) if choice == "create" else ())
