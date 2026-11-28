from __future__ import annotations

"""Seal one payer at a time, then give the committed total to canonical draws."""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from ..cost_payments import available_payment_mana_steps
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.mana_contribution_draw import (
    MANA_CONTRIBUTION_DRAW_CAPABILITIES,
    MANA_CONTRIBUTION_DRAW_OPERATION,
    ManaContribution,
    validate_mana_contribution_draw,
)
from ..rules.mana_pool_payment import MANA_POOL_PAYMENT_FIELD, payable_mana_pool_rows
from ..semantic_runtime.intents import PayManaCostIntent, RecordChoiceIntent
from ..turn_priority_model import apnap_participants
from ..util import stable_json
from .model import (
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


MANA_SELECTOR = "contribution_mana"
MANA_PENDING = "_contribution_mana_pending"
JOURNAL = "contributions"
_MAXIMUM = "_contribution_maximum"
_CHOICES = "_contribution_mana_choices"
_POOL_ROWS = "_contribution_mana_pool_rows"
CHOICE_METADATA = frozenset({"action", "choice_schema", "label"})


def contribution_instruction(effect):
    local = {JOURNAL, _MAXIMUM, _CHOICES, _POOL_ROWS, MANA_PENDING}
    return {key: value for key, value in effect.items() if key not in local}


def contribution_position(effect, controller, seats, active):
    instruction = contribution_instruction(effect)
    validate_mana_contribution_draw(instruction, resolved=True)
    if instruction["player"] != controller:
        raise ValueError("Mana contributions lost their original resolving controller")
    choices = effect.get(JOURNAL, ())
    if not isinstance(choices, (tuple, list)):
        raise ValueError("Mana contributions require an ordered journal")
    journal = tuple(ManaContribution.from_dict(row) for row in choices)
    ordered = apnap_participants(seats, active, controller)
    if (len(journal) >= len(ordered)
            or tuple(row.player for row in journal) != ordered[:len(journal)]):
        raise ValueError("Mana contributions must follow the original controller's turn order")
    return ordered[len(journal)], journal, ordered


def mana_options(host, actor):
    return available_payment_mana_steps(host, actor, spend_context=None, include_unspendable=True)


def mana_action(index, activation, name, schema):
    token = sha256(stable_json(activation).encode("utf-8")).hexdigest()
    symbols = "".join(f" {{{color}}} × {count}" if count != 1 else f" {{{color}}}"
                      for color, count in activation["bundle"].items())
    return FrozenMap({
        "id": f"mana-contribution:mana:{index}",
        "action": "choose",
        "label": f"Activate {name} for mana{symbols}",
        MANA_SELECTOR: token,
        **({"choice_schema": schema} if schema is not None else {}),
    })


def bind_mana_contribution_draw(host, item, effect):
    """Bind this instruction's next actor and their current, noncast quote."""
    if effect.get("op") != MANA_CONTRIBUTION_DRAW_OPERATION:
        return effect, None
    if {_MAXIMUM, _CHOICES, _POOL_ROWS, MANA_PENDING} & set(effect):
        raise ValueError("A resolving contribution must get a new payment quote")
    actor, journal, _ = contribution_position(effect, item.controller, host.seats, host.active_seats)
    pool = host._spendable_mana_pool(actor, None)
    return {
        **effect,
        JOURNAL: [row.to_dict() for row in journal],
        _MAXIMUM: sum(pool.values()),
        _POOL_ROWS: payable_mana_pool_rows(host.state.players[actor], host._mana_restriction_allows, None),
        _CHOICES: tuple(mana_action(index, activation, name, schema)
                        for index, (activation, name, schema) in enumerate(mana_options(host, actor))),
    }, actor


@dataclass(frozen=True, slots=True)
class _ContributionRequest(SemanticChoiceRequest):
    mana_actions: tuple[FrozenMap, ...] = ()

    def payload(self):
        payload = SemanticChoiceRequest.payload(self)
        payload["legal_actions"].extend(thaw_value(action) for action in self.mana_actions)
        return payload


def require_payable_quote(effect):
    maximum = effect.get(_MAXIMUM)
    if type(maximum) is not int or maximum < 0:
        raise ValueError("Mana contribution requires a current nonnegative payment quote")
    return maximum


def _contribution_intent(actor, chosen, continuation, pool_payment):
    event = "effect.mana_contribution.paid"
    message = f"{actor} contributed {chosen} mana."
    details = FrozenMap({"stack": continuation.stack_ref, "amount": chosen})
    if not chosen:
        return RecordChoiceIntent(actor, event, message, details)
    return PayManaCostIntent(
        actor, actor, FrozenMap({"GENERIC": chosen}), continuation.note, event, message, details,
        pool_payment=pool_payment,
    )


@dataclass(frozen=True, slots=True)
class ManaContributionDrawHandler:
    operation: str = MANA_CONTRIBUTION_DRAW_OPERATION
    handler_id: str = "choice.payment.mana-contribution-draw.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "101.1", "101.4b", "106.4a", "117.1d", "117.2e", "118.3", "121.2", "121.2c",
        "605.3a", "605.3b", "608.2c", "608.2g",
    )
    capability_dependencies: tuple[str, ...] = MANA_CONTRIBUTION_DRAW_CAPABILITIES
    continuation_fields: tuple[str, ...] = ("player", JOURNAL, _MAXIMUM, MANA_PENDING)
    private_data: tuple[str, ...] = (MANA_PENDING,)
    projected_fields: tuple[str, ...] = ("contributions", "stack", "operation", "legal_actions", "mana_pool_rows")
    mutation_path: tuple[str, ...] = (
        "mana_activation.complete_mana_plan_activations",
        "semantic_choices.intent_host.pay_mana_cost_intent",
        "drawing.coordinator.begin_draw_batch",
    )
    replay_fixture: str = "mana-contribution-draw"
    test_modules: tuple[str, ...] = ("tests.test_mana_contribution_draw",)

    def prepare(self, effect, context):
        try:
            actor, journal, _ = contribution_position(
                effect, context.stack_controller, context.query.seats, context.query.active_seats,
            )
            maximum = require_payable_quote(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if actor != context.actor:
            raise SemanticChoiceError("This mana contribution belongs to another player")
        return SemanticChoicePreparation(
            request=_ContributionRequest(
                prompt="Activate mana abilities or choose how much mana to contribute.",
                choice=ScalarChoice("x", value_type="integer", minimum=0, maximum=maximum),
                public_context=FrozenMap({
                    "stack": context.stack_ref, "operation": self.operation,
                    "contributions": [row.to_dict() for row in journal], "mana_pool_rows": effect[_POOL_ROWS],
                }),
                mana_actions=tuple(effect[_CHOICES]),
            ),
            continuation_effect=FrozenMap({
                key: value for key, value in effect.items() if key not in {_CHOICES, _POOL_ROWS}
            }),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        try:
            actor, journal, ordered = contribution_position(
                effect, continuation.semantic_frame.controller, query.seats, query.active_seats,
            )
            maximum = require_payable_quote(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if set(response) - CHOICE_METADATA - {"x", MANA_POOL_PAYMENT_FIELD}:
            raise SemanticChoiceError("Mana contribution accepts only the amount and an optional pool selection")
        chosen = response.get("x")
        if type(chosen) is not int or not 0 <= chosen <= maximum:
            raise SemanticChoiceError("Choose an integer contribution within the payable range")
        pool_payment = None
        if MANA_POOL_PAYMENT_FIELD in response:
            payment = response[MANA_POOL_PAYMENT_FIELD]
            if (not isinstance(payment, Mapping)
                    or any(type(key) is not str or not key or type(count) is not int or count <= 0
                           for key, count in payment.items())
                    or sum(payment.values()) != chosen):
                raise SemanticChoiceError("Select exactly the mana contributed from your own pool")
            pool_payment = FrozenMap(payment)
        journal = (*journal, ManaContribution(actor, chosen))
        intent = _contribution_intent(actor, chosen, continuation, pool_payment)
        if len(journal) < len(ordered):
            next_payment = FrozenMap({**contribution_instruction(effect), JOURNAL: [row.to_dict() for row in journal]})
            return SemanticChoiceCompletion(intents=(intent,), repeat_effect=next_payment)
        total = sum(row.amount for row in journal)
        draws = (FrozenMap({"op": "draw_each_player", "count": total, "private": True}),) if total else ()
        return SemanticChoiceCompletion(intents=(intent,), prepend_effects=draws)
