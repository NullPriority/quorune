"""Resolve the caster's zone decision before the shared search opens a library."""

from dataclasses import dataclass

from ..cast_x_search_model import (
    CAST_X_ZONE_SEARCH_CAPABILITY, CAST_X_ZONE_SEARCH_OPERATION, CAST_X_ZONE_SEARCH_RULES,
    CastXZoneSearchSelection, SEARCHED_ZONE_CHOICES, decode_zone_search,
)
from ..replacement.immutable import FrozenMap
from ..rules.library_search_rules import library_graveyard_search_choices
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


@dataclass(frozen=True, slots=True)
class CastXZoneSearchChoiceHandler:
    operation: str = CAST_X_ZONE_SEARCH_OPERATION
    handler_id: str = "choice.search.cast-x-library-graveyard.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = CAST_X_ZONE_SEARCH_RULES
    capability_dependencies: tuple[str, ...] = (CAST_X_ZONE_SEARCH_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "search", "_choice_actor", "_zone_options")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("SemanticChoiceCompletion.prepend_effects", "HiddenSearchOwnerMixin")
    replay_fixture: str = "cast-x-library-graveyard-creature-search"
    test_modules: tuple[str, ...] = ("tests.test_named_zone_searches",)

    def prepare(self, effect, context):
        try:
            maximum, from_entry = decode_zone_search(effect, actor=context.actor)
            if context.actor != context.stack_controller:
                raise ValueError("The caster must declare the searched zones")
            options = library_graveyard_search_choices(context.query.library_search_allowed(context.actor))
            kind = "non-Human creature" if from_entry else "creature"
            return SemanticChoicePreparation(
                request=SemanticChoiceRequest(
                    prompt=f"Choose where to search for a {kind} card with mana value {maximum} or less.",
                    choice=ScalarChoice(field_name="choice", legal_values=options),
                    public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation}),
                ),
                continuation_effect=FrozenMap({**effect, "_choice_actor": context.actor, "_zone_options": options}),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = effect.get("_choice_actor")
        try:
            if type(actor) is not str or not actor:
                raise ValueError("Announced-X search continuation actor is missing")
            maximum, from_entry = decode_zone_search(
                {key: effect[key] for key in ("op", "player", "search")}, actor=actor,
            )
            choice = response.get("choice")
            if (type(choice) is not str or choice not in effect["_zone_options"]
                    or choice not in library_graveyard_search_choices(query.library_search_allowed(actor))):
                raise ValueError("Choose an offered, currently legal searched-zone selection")
            search = CastXZoneSearchSelection(SEARCHED_ZONE_CHOICES[choice], maximum, non_human=from_entry).effect()
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap(search),))
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
