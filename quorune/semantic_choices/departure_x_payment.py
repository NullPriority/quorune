"""Announce and pay X before making the reflexive trigger's target choice."""
from dataclasses import dataclass
from ..replacement.immutable import FrozenMap
from ..rules.departing_x_reflexive import CAPABILITY, OPERATION, RED_DEATH_VERSION, PaidDepartureDamageIntent, payment_symbol
from ..semantic_runtime.intents import PayManaCostIntent
from .model import (SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
                    SemanticChoiceRequest, ScalarChoice)

_BASE = {"op","schema_version","player"}
_QUOTE = _BASE | {"stage","maximum"}
_TRANSPORT = {"choice","x","action","action_id","a","cap","reason","plan","next","choice_schema","label"}


def stage(effect):
    if (effect.get("op") != OPERATION or type(effect.get("schema_version")) is not int
            or effect["schema_version"] not in (1, RED_DEATH_VERSION) or type(effect.get("player")) is not str):
        raise SemanticChoiceError("The departure X payment has a closed instruction")
    if set(effect) == _BASE:
        return "decide"
    if (set(effect) == _QUOTE and effect["stage"] == "amount"
            and type(effect["maximum"]) is int and effect["maximum"] >= 0):
        return "pay"
    raise SemanticChoiceError("The departure X payment has a closed mana quote")


@dataclass(frozen=True, slots=True)
class DepartingXPaymentHandler:
    operation: str = OPERATION
    handler_id: str = "choice.cost.departure-reflexive-x.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("107.3","118.5","603.12","608.2d","608.2g")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)
    continuation_fields: tuple[str, ...] = tuple(sorted(_QUOTE))
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt","legal_actions.choice_schema.minimum","legal_actions.choice_schema.maximum")
    mutation_path: tuple[str, ...] = ("PayManaCostIntent","PaidDepartureDamageIntent")
    replay_fixture: str = "departed-electro-x-price-and-reflexive-target"
    test_modules: tuple[str, ...] = ("tests.test_departure_x_reflexive",)

    def prepare(self,effect,context):
        phase=stage(effect)
        if effect["player"] != context.actor or context.actor != context.stack_controller:
            raise SemanticChoiceError("Only the original resolving controller may pay X")
        red_death = effect["schema_version"] == RED_DEATH_VERSION
        if phase=="decide":
            prompt="Pay red mana to trigger the damage ability?" if red_death else "Pay X mana to trigger the damage ability?"
            choice=ScalarChoice("choice",("pay","decline"))
        else:
            prompt="Choose how much red mana to pay." if red_death else "Choose the generic mana to pay."
            choice=ScalarChoice("x",value_type="integer",minimum=0,maximum=effect["maximum"])
        return SemanticChoicePreparation(SemanticChoiceRequest(prompt,choice,
            FrozenMap({"stack":context.stack_ref,"payment_kind":"mana"})),FrozenMap(effect))

    def complete(self,continuation,response,query):
        effect=continuation.effect
        phase=stage(effect)
        payer=continuation.semantic_frame.controller
        if effect["player"] != payer or payer not in query.active_seats or set(response)-_TRANSPORT:
            raise SemanticChoiceError("The departure mana payment lost its original controller")
        if phase=="decide":
            choice=response.get("choice")
            if "x" in response or choice not in ("pay","decline"):
                raise SemanticChoiceError("Choose pay or decline first")
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap({**effect,"stage":"amount"}),) if choice=="pay" else ())
        amount=response.get("x")
        if type(amount) is not int or not 0 <= amount <= effect["maximum"]:
            raise SemanticChoiceError("Choose a nonnegative mana payment in the current offered range")
        requirements=FrozenMap({payment_symbol(effect["schema_version"]): amount})
        payment=(PayManaCostIntent(actor=payer,player=payer,requirements=requirements,reason=continuation.note,
            event_code="optional_cost.paid",message=f"{payer} paid {amount} mana.",
            details=FrozenMap({"cost":requirements,"stack":continuation.stack_ref})),) if amount else ()
        return SemanticChoiceCompletion(intents=(*payment,PaidDepartureDamageIntent(payer,continuation.stack_ref,amount)))
