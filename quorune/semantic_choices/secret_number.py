"""Prepare one private table decision and release its ordered effects on completion."""

from __future__ import annotations

from dataclasses import dataclass

from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, AllocatedDamageRecipient
from ..replacement.immutable import FrozenMap
from ..rules.secret_number_choices import DEPENDENCIES, OPERATION, REVEAL_EVENT, validate_extreme_number_instruction
from ..semantic_runtime.intents import RecordChoiceIntent
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


def _read_choices(response, seats):
    choices = response.get("number_commitments")
    if (set(response) != {"number_commitments"} or not isinstance(choices, tuple)
            or len(choices) != len(seats) or any(
                not isinstance(row, tuple) or len(row) != 2 or row[0] != seat
                or type(row[1]) is not int or row[1] < 0
                for row, seat in zip(choices, seats))):
        raise SemanticChoiceError("Secret number completion requires every current player exactly once")
    return choices


def _extreme_effects(effect, choices, lowest, highest):
    effects = []
    if highest:
        recipients = tuple(AllocatedDamageRecipient(seat, None, highest).to_dict()
                           for seat, number in choices if number == highest)
        effects.append(FrozenMap({
            "op": "damage_fixed_set", "source": "$source",
            "amount": highest * len(recipients), ANNOUNCED_DAMAGE_EFFECT_FIELD: recipients,
        }))
    redrawers = tuple(seat for seat, number in choices if number != lowest)
    if redrawers:
        effects.extend((
            FrozenMap({"op": "discard_whole_hands", "actor": effect["player"], "players": redrawers}),
            FrozenMap({"op": "draw_each_player", "players": redrawers, "count": effect["count"], "private": True}),
        ))
    return tuple(effects)


@dataclass(frozen=True, slots=True)
class SecretNumberExtremesHandler:
    operation: str = OPERATION
    handler_id: str = "choice.secret-number-extremes.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "101.1", "101.4", "107.1a", "107.1b", "120.4", "121.2c", "608.2c", "608.2e", "701.9a", "704.4",
    )
    capability_dependencies: tuple[str, ...] = DEPENDENCIES
    continuation_fields: tuple[str, ...] = ("player", "count")
    private_data: tuple[str, ...] = ("number_commitments",)
    projected_fields: tuple[str, ...] = ("stack", "operation", "legal_actions")
    mutation_path: tuple[str, ...] = (
        "RecordChoiceIntent", "damage_fixed_set", "discard_whole_hands", "draw_each_player",
    )
    replay_fixture: str = "secret-number-extremes"
    test_modules: tuple[str, ...] = ("tests.test_secret_number_extremes",)

    def prepare(self, effect, context):
        validate_extreme_number_instruction(effect, materialized=True)
        seats = context.query.apnap_order
        if context.actor != context.stack_controller or effect["player"] != context.actor or not seats:
            raise SemanticChoiceError("Secret number choices require the resolving controller and active seats")
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                "Secretly choose a whole number 0 or greater.",
                ScalarChoice(field_name="number", value_type="string", nonempty=True),
                FrozenMap({"stack": context.stack_ref, "operation": OPERATION}),
                simultaneous_actors=seats,
            ),
            continuation_effect=FrozenMap(effect),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        validate_extreme_number_instruction(effect, materialized=True)
        if effect["player"] != continuation.semantic_frame.controller:
            raise SemanticChoiceError("Secret number choices changed the resolving controller")
        choices = _read_choices(response, query.apnap_order)
        lowest = min(number for _, number in choices)
        highest = max(number for _, number in choices)
        published = tuple((seat, str(number)) for seat, number in choices)
        reveal = RecordChoiceIntent(
            effect["player"], REVEAL_EVENT, REVEAL_EVENT,
            FrozenMap({"choices": published, "lowest": str(lowest), "highest": str(highest)}),
        )
        return SemanticChoiceCompletion(
            intents=(reveal,), prepend_effects=_extreme_effects(effect, choices, lowest, highest),
        )
