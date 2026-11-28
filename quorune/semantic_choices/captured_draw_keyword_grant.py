"""Sample one public creature set before handing off to the ordinary draw owner."""

from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..object_query import query_objects
from ..rules.captured_counter_keyword_grant import (
    CAPTURED_COUNTER_KEYWORD_CAPABILITY, CAPTURED_COUNTER_KEYWORD_GRANT_FIELD,
    CAPTURED_COUNTER_KEYWORD_OPERATION, CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION,
    decode_captured_counter_keyword_instruction,
)
from ..semantic_runtime.intents import PlaceCountersIntent
from ..replacement.immutable import FrozenMap
from ..rules.captured_draw_keyword_grant import (
    CAPTURED_DRAW_KEYWORD_CAPABILITY, CAPTURED_DRAW_KEYWORD_OPERATION,
    decode_captured_draw_keyword_instruction, retained_keyword_grant_effect,
)
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


def _captured_rows(context, query):
    rows = query_objects(context.query.objects(zones=("battlefield",), controller=context.actor), query)
    return tuple(sorted(rows, key=lambda row: (row.object_id, row.logical_object_id)))


def _captured_identities(rows):
    return tuple(ContinuousObjectIdentity(row.object_id, row.logical_object_id) for row in rows)


@dataclass(frozen=True, slots=True)
class CapturedDrawKeywordGrantHandler:
    operation: str = CAPTURED_DRAW_KEYWORD_OPERATION
    handler_id: str = "choice.draw.captured-keyword-grant.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 121.2", "CR 608.2c", "CR 608.2h", "CR 611.2c")
    capability_dependencies: tuple[str, ...] = (CAPTURED_DRAW_KEYWORD_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "query")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("draw coordinator", "create_resolution_continuous_effect_components")
    replay_fixture: str = "captured-draw-keyword-grant"
    test_modules: tuple[str, ...] = ("tests.test_captured_draw_keyword_grant",)

    def prepare(self, effect, context):
        actor = context.actor
        if actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("The original spell controller owns the captured draw")
        try:
            query = decode_captured_draw_keyword_instruction(effect, player=actor)
            identities = _captured_identities(_captured_rows(context, query))
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        effects = ()
        if identities:
            effects = (
                FrozenMap({"op": "draw", "player": actor, "count": len(identities), "private": True}),
                FrozenMap(retained_keyword_grant_effect(player=actor, identities=identities)),
            )
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("Use the sampled creature set for both instructions", prepend_effects=effects))

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("Capturing draw and grant subjects issues no player choice")


@dataclass(frozen=True, slots=True)
class CapturedCounterKeywordGrantHandler:
    operation: str = CAPTURED_COUNTER_KEYWORD_OPERATION
    handler_id: str = "choice.counter.captured-keyword-grant.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("122.1a", "608.2c", "608.2h", "611.2c", "702.20")
    capability_dependencies: tuple[str, ...] = (CAPTURED_COUNTER_KEYWORD_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "query")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("PlaceCountersIntent", "create_resolution_continuous_effect_components")
    replay_fixture: str = "captured-counter-keyword-grant"
    test_modules: tuple[str, ...] = ("tests.test_captured_counter_keyword_grant",)

    def prepare(self, effect, context):
        actor = context.actor
        if actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("The resolving controller owns the captured counter placement")
        try:
            query = decode_captured_counter_keyword_instruction(effect, player=actor)
            rows = _captured_rows(context, query)
            identities = _captured_identities(rows)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        intents = ()
        effects = ()
        version = effect["schema_version"]
        announcement = ("Grant vigilance to the same captured creatures" if version == 1
                        else "Grant Flying to the same captured creatures")
        if version == CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION:
            announcement = f"Grant {effect['keyword'].casefold()} to the same captured creatures"
        if rows:
            intents = (PlaceCountersIntent(
                actor=actor, object_refs=tuple(row.ref for row in rows),
                counter_name="+1/+1", amount=1, reason=context.stack_label, source_ref=context.source_ref,
            ),)
            effects = (FrozenMap(retained_keyword_grant_effect(
                player=actor, identities=identities, field=CAPTURED_COUNTER_KEYWORD_GRANT_FIELD, version=version,
            )),)
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
            auto_continue=AutoContinue(announcement, prepend_effects=effects),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("Capturing counter and keyword subjects issues no player choice")
