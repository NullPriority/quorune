"""Sample one public creature set before handing off to the ordinary draw owner."""

from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..object_query import query_objects
from ..replacement.immutable import FrozenMap
from ..rules.captured_draw_keyword_grant import (
    CAPTURED_DRAW_KEYWORD_CAPABILITY, CAPTURED_DRAW_KEYWORD_OPERATION,
    decode_captured_draw_keyword_instruction, retained_keyword_grant_effect,
)
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


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
            rows = query_objects(context.query.objects(zones=("battlefield",), controller=actor), query)
            identities = tuple(sorted(ContinuousObjectIdentity(row.object_id, row.logical_object_id) for row in rows))
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
