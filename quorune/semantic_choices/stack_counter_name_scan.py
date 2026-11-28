"""Bind the target spell before an ordered counter and library continuation."""

from dataclasses import dataclass

from ..carddb_characteristics import public_exile_rules_names
from ..discover_model import ExileCastProgress, discover_zone_counter
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.stack_counter_name_scan import COUNTER_NAME_SCAN_OPERATION, counter_name_scan_instructions
from ..stack_spell_name_scan_model import (
    NAME_SCAN_CAPABILITY, NAME_SCAN_CAST_OPERATION, NAME_SCAN_FINISH_OPERATION, NAME_SCAN_OPERATION,
    NAME_SCAN_STEP_OPERATION, StackSpellNameScan,
)
from .discover import DiscoverScanHandler
from .discover_cast import DiscoverCastChoiceHandler
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


_RULES = ("201.2b", "400.7j", "608.2b", "608.2c", "608.2d", "608.2g", "609.3", "701.6a", "701.13a", "701.17a")
_EVIDENCE = {
    "rule_references": _RULES,
    "capability_dependencies": (NAME_SCAN_CAPABILITY,),
    "replay_fixture": "counter-spell-affected-player-different-name-exile-cast",
    "test_modules": ("tests.test_stack_counter_name_scan",),
}


@dataclass(frozen=True, slots=True)
class CounterNameScanHandler:
    operation: str = COUNTER_NAME_SCAN_OPERATION
    handler_id: str = "choice.effect.stack-counter-different-name-exile.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = _RULES
    capability_dependencies: tuple[str, ...] = (NAME_SCAN_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "scan", "mill_count")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("AutoContinue.prepend_effects", "CommanderEngine._continue_resolution")
    replay_fixture: str = "counter-spell-affected-player-different-name-exile-cast"
    test_modules: tuple[str, ...] = ("tests.test_stack_counter_name_scan",)

    def prepare(self, effect, context):
        if context.actor != context.stack_controller:
            raise SemanticChoiceError("The original resolving controller must counter the spell")
        try:
            scan = StackSpellNameScan.from_dict(effect["scan"])
            stack = context.query.stack_object(context.stack_ref)
            if stack is None:
                raise ValueError("The counter spell left its original resolution")
            scan.require_resolution(actor=scan.actor, stack_ref=stack.ref, targets=stack.targets)
            effects = counter_name_scan_instructions(effect, active_seats=context.query.active_seats)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            None, FrozenMap(effect),
            auto_continue=AutoContinue(
                reason="Counter the spell before its controller mills and exiles cards.",
                prepend_effects=tuple(FrozenMap(child) for child in effects),
            ),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("The random amount is chosen by the resolving instruction")


def materialize_name_scan_exile_names(host, effect, actor):
    if effect.get("op") != NAME_SCAN_STEP_OPERATION:
        return {}
    progress = ExileCastProgress.from_dict(effect["progress"])
    if progress.name_scan is None or progress.name_scan.actor != actor:
        raise SemanticChoiceError("The differently named exile scan changed its affected player")
    pending = LibrarySelectionObjectIdentity.from_dict(effect["pending"])
    card = host.state.cards.get(pending.object_id)
    if card is None or card.zone != "exile":
        return {}
    expected = f"{pending.object_id}@{discover_zone_counter(pending) + 1}"
    if card.ref != pending.ref or card.logical_object_id != expected or card.owner != actor:
        raise SemanticChoiceError("The exile scan changed the committed card")
    return {card.ref: public_exile_rules_names(card, host.card_record(card))}


STACK_COUNTER_NAME_SCAN_HANDLERS = (
    CounterNameScanHandler(),
    DiscoverScanHandler(
        NAME_SCAN_OPERATION, "choice.library.exile-until-different-name-start.v1",
        continuation_fields=("schema_version", "player", "scan"), **_EVIDENCE,
    ),
    DiscoverScanHandler(
        NAME_SCAN_STEP_OPERATION, "choice.library.exile-until-different-name-step.v1", **_EVIDENCE,
    ),
    DiscoverCastChoiceHandler(
        NAME_SCAN_CAST_OPERATION, "choice.library.exile-until-different-name-cast.v1",
        mutation_path=("DiscoverCastIntent",), **_EVIDENCE,
    ),
    DiscoverCastChoiceHandler(
        NAME_SCAN_FINISH_OPERATION, "choice.library.exile-until-different-name-finish.v1",
        mutation_path=("DiscoverCleanupIntent",), **_EVIDENCE,
    ),
)
