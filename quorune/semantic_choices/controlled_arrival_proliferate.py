"""Snapshot the live arrival count and use the ordinary Proliferate owner for each decision."""

from dataclasses import dataclass

from ..counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD
from ..replacement.immutable import FrozenMap
from ..rules.controlled_arrival_proliferate import (
    CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY, CONTROLLED_ARRIVAL_PROLIFERATE_OPERATION,
    controlled_arrival_proliferate_effect,
)
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


_PROLIFERATE_ONCE = FrozenMap({"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1})


@dataclass(frozen=True, slots=True)
class ControlledArrivalProliferateHandler:
    operation: str = CONTROLLED_ARRIVAL_PROLIFERATE_OPERATION
    handler_id: str = "choice.counter.proliferate-for-controlled-nontoken-arrivals.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 608.2h", "CR 701.34a", "CR 122.6")
    capability_dependencies: tuple[str, ...] = (CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player",)
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("ProliferateChoiceHandler", "ProliferateIntent")
    replay_fixture: str = "tromell-entry-counter-proliferate"
    test_modules: tuple[str, ...] = ("tests.test_tromell_entry_counter_proliferate",)

    def prepare(self, effect, context):
        if (set(effect) != set(controlled_arrival_proliferate_effect())
                or effect["player"] != context.actor or context.actor != context.stack_controller
                or context.actor not in context.query.active_seats):
            raise SemanticChoiceError("Repeated Proliferate requires the original resolving controller")
        quantity = sum(row.entered_this_turn and not row.token and "creature" in row.types
                       for row in context.query.objects(zones=("battlefield",), controller=context.actor))
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("Proliferate for the current nontoken creature arrivals",
                                       prepend_effects=(_PROLIFERATE_ONCE,) * quantity))

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("The arrival count has no player choice")
