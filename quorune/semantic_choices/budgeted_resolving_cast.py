"""The authorized resolving cast loop for one original two-spell permission."""
from collections.abc import Mapping
from dataclasses import dataclass
from ..discover_model import discover_options_fingerprint
from ..replacement.immutable import FrozenMap
from ..resolving_cast_budget_model import CAPABILITY,CONTINUATION_FIELDS,OPERATION,ResolveBudgetedCastIntent,validate_effect
from .model import (AutoContinue,CastOrDeclineChoice,SemanticChoiceCompletion,
                    SemanticChoiceError,SemanticChoicePreparation,SemanticChoiceRequest)


@dataclass(frozen=True, slots=True)
class BudgetedHandGraveyardCastHandler:
    operation: str = OPERATION
    handler_id: str = "choice.cast.budgeted-hand-graveyard.v1"
    schema_version: int = 1
    rule_references: tuple[str,...] = ("107.3b","202.3","601.2","608.2d","608.2g","614.1a")
    capability_dependencies: tuple[str,...] = (CAPABILITY,)
    continuation_fields: tuple[str,...] = ("player","cast_limit","mana_value_limit","_options_fingerprint")
    private_data: tuple[str,...] = ("unplayed hand candidates",)
    projected_fields: tuple[str,...] = ("prompt","cast_options","legal_actions.choice_schema")
    mutation_path: tuple[str,...] = ("ResolveBudgetedCastIntent","commit_resolving_cast","mark_cast_grant_rider")
    replay_fixture: str = "invoke-calamity-cumulative-cast-and-rider"
    test_modules: tuple[str,...] = ("tests.test_budgeted_resolving_cast",)

    def prepare(self,effect,context):
        validate_effect(effect,actor=context.actor)
        if context.actor!=context.stack_controller:
            raise SemanticChoiceError("The original resolving controller chooses these casts")
        options=tuple(context.query.authorized_cast_options())
        if not options:
            return SemanticChoicePreparation(None,FrozenMap(effect),auto_continue=AutoContinue("No further eligible cast"))
        return SemanticChoicePreparation(
            SemanticChoiceRequest("Cast one offered spell, or finish resolving.",CastOrDeclineChoice(options),
                FrozenMap({"stack":context.stack_ref,"operation":self.operation,"cast_options":options})),
            FrozenMap({**effect,"_options_fingerprint":discover_options_fingerprint(options)}),
        )

    def complete(self,continuation,response,query):
        saved=continuation.effect
        actor=continuation.semantic_frame.controller
        instruction={key:value for key,value in saved.items() if key not in CONTINUATION_FIELDS}
        validate_effect(instruction,actor=actor)
        options=tuple(query.authorized_cast_options())
        if discover_options_fingerprint(options)!=saved.get("_options_fingerprint"):
            raise SemanticChoiceError("The current cast options changed")
        choice=response.get("choice")
        cast=response.get("cast")
        extras={key:value for key,value in response.items() if key not in {"action","action_id","choice","cast","choice_schema"}}
        if choice=="decline" and cast is None and not extras:
            return SemanticChoiceCompletion()
        if choice!="cast" or (cast is not None and not isinstance(cast,Mapping)) or set(cast or ()) & extras.keys():
            raise SemanticChoiceError("Choose one current cast with each field supplied once")
        submitted={**(cast or {}),**extras}
        option=submitted.get("cost_option")
        if option is None and len(options)==1:
            submitted["cost_option"]=options[0]["id"]
        intent=ResolveBudgetedCastIntent(actor,continuation.stack_ref,saved["_options_fingerprint"],FrozenMap(submitted))
        return SemanticChoiceCompletion(intents=(intent,),prepend_effects=(FrozenMap(instruction),))
