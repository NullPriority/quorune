"""Announce searched zones, then let the existing search owner see that set."""

from dataclasses import dataclass

from ..named_search_model import (
    NAMED_ZONE_SEARCH_CAPABILITY, NAMED_ZONE_SEARCH_OPERATION, NAMED_ZONE_SEARCH_RULES,
    decode_named_zone_search,
)
from ..replacement.immutable import FrozenMap
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


@dataclass(frozen=True, slots=True)
class NamedZoneSearchChoiceHandler:
    operation: str = NAMED_ZONE_SEARCH_OPERATION
    handler_id: str = "choice.search.named-library-graveyard.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = NAMED_ZONE_SEARCH_RULES
    capability_dependencies: tuple[str, ...] = (NAMED_ZONE_SEARCH_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "search", "_choice_actor", "_zone_options")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("SemanticChoiceCompletion.prepend_effects", "HiddenSearchOwnerMixin")
    replay_fixture: str = "named-library-graveyard-search"
    test_modules: tuple[str, ...] = ("tests.test_named_zone_searches",)

    def prepare(self, effect, context):
        try:
            spec = decode_named_zone_search(effect, actor=context.actor)
            if context.actor != context.stack_controller:
                raise ValueError("Named zone search requires the resolving controller")
            options = (("library", "graveyard", "both") if context.query.library_search_allowed(context.actor)
                       else ("graveyard",))
            if spec.optional:
                options += ("decline",)
            return SemanticChoicePreparation(
                request=SemanticChoiceRequest(prompt=f"Choose where to search for {spec.name}.",
                    choice=ScalarChoice(field_name="choice", legal_values=options),
                    public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation})),
                continuation_effect=FrozenMap({**dict(effect), "_choice_actor": context.actor, "_zone_options": options}))
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = effect.get("_choice_actor")
        try:
            if type(actor) is not str or not actor:
                raise ValueError("Named search continuation actor is missing")
            spec = decode_named_zone_search({key: effect[key] for key in ("op", "player", "search")}, actor=actor)
            choice = response.get("choice")
            if type(choice) is not str or choice not in effect["_zone_options"]:
                raise ValueError("Named search zone was not offered")
            if choice == "decline":
                if not spec.optional:
                    raise ValueError("This named search cannot be declined")
                return SemanticChoiceCompletion()
            if choice in {"library", "both"} and not query.library_search_allowed(actor):
                raise ValueError("Library search is currently prohibited")
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap(spec.search_instruction(choice)),))
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
