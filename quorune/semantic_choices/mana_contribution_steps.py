from __future__ import annotations

"""Resume a particular payer's mana abilities within a resolving instruction."""

from collections.abc import Mapping
from dataclasses import replace

from ..artifact_hand_mana_model import (
    ARTIFACT_HAND_MANA_REVEAL_FIELD,
    ARTIFACT_HAND_MANA_STEP,
    ArtifactHandManaStep,
)
from ..cost_payments import ExactManaReplacementReplay, mana_step_replacement_event_id
from ..errors import GameRuleError
from ..mana_activation import complete_mana_plan_activations
from ..mana_mode_effects import mana_sacrifice_payment_event_id
from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement.ordering import ReplacementChoiceRequired, replacement_choice_payload
from ..rules.mana_contribution_draw import MANA_CONTRIBUTION_DRAW_OPERATION
from .mana_contribution_draw import (
    CHOICE_METADATA,
    JOURNAL,
    MANA_PENDING,
    MANA_SELECTOR,
    contribution_instruction,
    contribution_position,
    mana_action,
    mana_options,
    require_payable_quote,
)
from .preparation_coordination import issue_semantic_choice


def _selected_mana_activation(host, decision, actor, response):
    if set(response) - CHOICE_METADATA - {MANA_SELECTOR, ARTIFACT_HAND_MANA_REVEAL_FIELD}:
        raise GameRuleError("A mana activation cannot also choose a contribution")
    token = response.get(MANA_SELECTOR)
    for index, (activation, name, schema) in enumerate(mana_options(host, actor)):
        action = mana_action(index, activation, name, schema)
        if token == action[MANA_SELECTOR] and thaw_value(action) in decision.payload_by_actor[actor]["legal_actions"]:
            return _with_artifact_selection(activation, response)
    raise GameRuleError("Choose a current mana ability of the contributing player")


def _with_artifact_selection(activation, response):
    if ARTIFACT_HAND_MANA_REVEAL_FIELD not in response:
        return activation
    if ARTIFACT_HAND_MANA_STEP not in activation:
        raise GameRuleError("This mana ability has no hand selection")
    try:
        artifact = ArtifactHandManaStep(
            activation[ARTIFACT_HAND_MANA_STEP]["ability_id"], response[ARTIFACT_HAND_MANA_REVEAL_FIELD],
        )
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return {**activation, ARTIFACT_HAND_MANA_STEP: artifact.to_dict()}


def _apply_mana(host, actor, activation, payment_id, selections):
    sacrifice = mana_sacrifice_payment_event_id(payment_id, activation["source"])
    replay = ExactManaReplacementReplay(selections, payment_id, extra_event_ids={sacrifice})
    with host.transaction():
        complete_mana_plan_activations(
            host, actor, [activation], payment_id=payment_id,
            replacement_selections_by_event=replay, payment_window=True,
        )
        replay.validate()


def _issue_mana_replacement(host, continuation, required, *, actor, activation, payment_id, selections):
    event_id = mana_step_replacement_event_id(required, actor, activation["source"], payment_id)
    chooser = required.pending.choice.chooser
    payload = replacement_choice_payload(required.pending, required.effects)
    effect = FrozenMap({**dict(continuation.effect), MANA_PENDING: {
        "activation": activation, "payment_id": payment_id,
        "selections": selections, "event_id": event_id,
    }})
    issue_semantic_choice(host, replace(continuation, effect=effect), chooser, payload)


def _resume_mana(host, item, continuation, actor, activation, payment_id, selections):
    try:
        _apply_mana(host, actor, activation, payment_id, selections)
    except ReplacementChoiceRequired as required:
        _issue_mana_replacement(
            host, continuation, required, actor=actor, activation=activation,
            payment_id=payment_id, selections=selections,
        )
        return
    effect = {**contribution_instruction(continuation.effect), JOURNAL: thaw_value(continuation.effect[JOURNAL])}
    host._begin_semantic_choice(
        item=item, effect=effect, remaining=continuation.remaining, destination=continuation.destination,
        note=continuation.note, instruction_pointer=continuation.semantic_frame.instruction_pointer,
    )


def _complete_mana_replacement(host, decision, item, continuation, actor, pending, response):
    from ..replacement_decisions import _replacement_selection

    if (not isinstance(pending, Mapping)
            or set(pending) != {"activation", "payment_id", "selections", "event_id"}
            or not isinstance(pending["activation"], Mapping) or not isinstance(pending["selections"], Mapping)
            or type(pending["payment_id"]) is not str or type(pending["event_id"]) is not str
            or set(response) - CHOICE_METADATA - {
                "replacement", "replacement_event", "replacement_objects", "prevention_allocation",
            }):
        raise GameRuleError("Mana contribution replacement fields are malformed")
    activation, payment_id = thaw_value(pending["activation"]), pending["payment_id"]
    selections = thaw_value(pending["selections"])
    try:
        with host.transaction():
            _apply_mana(host, actor, activation, payment_id, selections)
            raise GameRuleError("The mana contribution replacement is no longer available")
    except ReplacementChoiceRequired as required:
        event_id = mana_step_replacement_event_id(required, actor, activation["source"], payment_id)
        if event_id != pending["event_id"] or decision.actors != [required.pending.choice.chooser]:
            raise GameRuleError("Mana contribution replacement authority changed")
        selection = _replacement_selection(response, required.pending, error_type=GameRuleError)
        selections[event_id] = [*selections.get(event_id, ()), selection]
    _resume_mana(host, item, continuation, actor, activation, payment_id, selections)


def complete_contribution_mana_choice(host, decision, item, continuation, response):
    """Enforce the payer, then handle mana before the unchanged amount choice."""
    effect = continuation.effect
    if effect.get("op") != MANA_CONTRIBUTION_DRAW_OPERATION:
        return False
    try:
        actor, _, _ = contribution_position(effect, item.controller, host.seats, host.active_seats)
        maximum = require_payable_quote(effect)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if MANA_PENDING in effect:
        _complete_mana_replacement(host, decision, item, continuation, actor, effect[MANA_PENDING], response)
        return True
    if decision.actors != [actor] or maximum != sum(host._spendable_mana_pool(actor, None).values()):
        raise GameRuleError("The contribution payer or mana quote changed")
    if MANA_SELECTOR not in response:
        return False
    activation = _selected_mana_activation(host, decision, actor, response)
    payment_id = f"contribution:{continuation.stack_ref}:{decision.decision_id}"
    _resume_mana(host, item, continuation, actor, activation, payment_id, {})
    return True
