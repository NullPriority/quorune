"""Release a settled counter threshold through the existing effect owners."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.life_gain_counter_reset import (
    LIFE_GAIN_COUNTER_RESET_CAPABILITY, LIFE_GAIN_COUNTER_RESET_OPERATION, resolved_counter_reset,
)
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


@dataclass(frozen=True, slots=True)
class LifeGainCounterResetHandler:
    operation: str = LIFE_GAIN_COUNTER_RESET_OPERATION
    handler_id: str = "choice.counters.life-gain-threshold-reset.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("119.9", "122.1", "122.1d", "400.7", "608.2c", "614.6")
    capability_dependencies: tuple[str, ...] = (LIFE_GAIN_COUNTER_RESET_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "source", "counter", "minimum", "observed")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("AutoContinue.prepend_effects", "remove_all_counters", "untap")
    replay_fixture: str = "life-gain-counter-threshold-reset"
    test_modules: tuple[str, ...] = ("tests.test_life_gain_counted_results",)

    def prepare(self, effect, context):
        try:
            releases = resolved_counter_reset(effect, actor=context.actor, stack_controller=context.stack_controller)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        card = (context.query.object(effect["source"], zones=("battlefield",))
                if releases and effect["source"] is not None else None)
        children = ()
        if (card is not None and not card.phased_out and card.object_id == context.source_object_id
                and card.logical_object_id == context.source_logical_object_id):
            children = (
                FrozenMap({"op": "remove_all_counters", "schema_version": 2,
                           "card": "$source.zone_object", "counter": effect["counter"], "source": "$source"}),
                FrozenMap({"op": "untap", "card": "$source.zone_object"}),
            )
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="Check the original creature after the counter placement.",
                                       prepend_effects=children),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("Named counter release issues no player choice")
