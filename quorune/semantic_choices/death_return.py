from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..death_return import (
    DeathReturnError,
    DeathReturnSpec,
    PERSIST_KEYWORD,
    UNDYING_KEYWORD,
    death_return_condition_holds,
)
from ..entry_counter_model import EffectEntryCounter
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import ZoneMoveIntent
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
)


_FIELDS = {
    "op",
    "player",
    "card",
    "expected_zone_change_counter",
    "departure_counters",
    "prohibited_counter",
    "entry_counter",
    "source",
    "rule_id",
}


def _spec(effect: Mapping[str, Any]) -> DeathReturnSpec:
    prohibited = effect.get("prohibited_counter")
    entry = effect.get("entry_counter")
    rule_id = effect.get("rule_id")
    matching = tuple(
        candidate
        for candidate in (
            DeathReturnSpec.for_keyword(PERSIST_KEYWORD),
            DeathReturnSpec.for_keyword(UNDYING_KEYWORD),
        )
        if (
            prohibited,
            entry,
            rule_id,
        )
        == (
            candidate.prohibited_counter,
            candidate.entry_counter,
            candidate.rule_id,
        )
    )
    if len(matching) != 1:
        raise SemanticChoiceError(
            "Death-return counters and rule identity are not canonical"
        )
    return matching[0]


@dataclass(frozen=True, slots=True)
class DeathReturnWithCounterHandler:
    """Prepare one identity-pinned return and its nested entry counter."""

    operation: str = "death_return_with_counter"
    handler_id: str = "choice.zone.death-return-counter.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 400.7",
        "CR 603.4",
        "CR 603.6c",
        "CR 603.10a",
        "CR 603.10e",
        "CR 702.79a",
        "CR 702.93a",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.effect_entry",
    )
    continuation_fields: tuple[str, ...] = (
        "card",
        "expected_zone_change_counter",
        "departure_counters",
        "prohibited_counter",
        "entry_counter",
        "source",
        "rule_id",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = (
        "ZoneMoveIntent",
        "prepare_zone_change_replacement",
        "commit_counter_events_from_resolution",
    )
    replay_fixture: str = "persist-undying-death-return"
    test_modules: tuple[str, ...] = (
        "tests.test_persist_undying_rules",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if not isinstance(effect, Mapping) or set(effect) != _FIELDS:
            missing = sorted(_FIELDS - set(effect))
            unknown = sorted(set(effect) - _FIELDS)
            details = [
                *(f"missing {field}" for field in missing),
                *(f"unknown {field}" for field in unknown),
            ]
            raise SemanticChoiceError(
                "Death-return effect fields: " + "; ".join(details)
            )
        if effect.get("op") != self.operation:
            raise SemanticChoiceError("Death-return operation changed")
        player = effect.get("player")
        card_ref = effect.get("card")
        source_ref = effect.get("source")
        incarnation = effect.get("expected_zone_change_counter")
        counters = effect.get("departure_counters")
        if (
            type(player) is not str
            or player != context.actor
            or player not in context.query.active_seats
            or type(card_ref) is not str
            or not card_ref
            or type(source_ref) is not str
            or not source_ref
            or type(incarnation) is not int
            or incarnation < 0
            or not isinstance(counters, Mapping)
        ):
            raise SemanticChoiceError(
                "Death-return identity or last-known facts are malformed"
            )
        try:
            spec = _spec(effect)
            condition_holds = death_return_condition_holds(
                counters,
                spec.prohibited_counter,
            )
        except DeathReturnError as exc:
            raise SemanticChoiceError(str(exc)) from exc

        card = context.query.object(card_ref, zones=("graveyard",))
        can_return = bool(
            condition_holds
            and card is not None
            and card.owner in context.query.active_seats
            and not card.token
        )
        intent = (
            ZoneMoveIntent(
                actor=player,
                object_ref=card_ref,
                expected_zones=("graveyard",),
                destination="battlefield",
                reason=context.stack_label,
                new_controller=card.owner,
                semantic_events=True,
                optional_if_missing=True,
                expected_zone_change_counter=incarnation,
                effect_entry_counters=(
                    EffectEntryCounter(
                        counter_name=spec.entry_counter,
                        amount=1,
                        placing_player=card.owner,
                        source_ref=source_ref,
                        rule_id=spec.rule_id,
                    ),
                ),
            )
            if can_return
            else None
        )
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(
                {
                    "op": self.operation,
                    "player": player,
                    "card": card_ref,
                    "expected_zone_change_counter": incarnation,
                    "departure_counters": dict(counters),
                    "prohibited_counter": spec.prohibited_counter,
                    "entry_counter": spec.entry_counter,
                    "source": source_ref,
                    "rule_id": spec.rule_id,
                }
            ),
            preparation_intents=((intent,) if intent is not None else ()),
            auto_continue=AutoContinue(
                reason=(
                    f"{spec.keyword} returned the same graveyard incarnation"
                    if can_return
                    else f"{spec.keyword} return condition no longer applies"
                )
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        raise SemanticChoiceError(
            "Death-return preparation never issues a player choice"
        )


DEATH_RETURN_CHOICE_HANDLERS = (DeathReturnWithCounterHandler(),)


__all__ = [
    "DEATH_RETURN_CHOICE_HANDLERS",
    "DeathReturnWithCounterHandler",
]
