"""Lower a closed library-bottom operation through the committed move owner."""

from dataclasses import dataclass

from ..library_bottom_result_model import (
    LIBRARY_BOTTOM_RESULT_OPERATION, LIBRARY_BOTTOM_RESULT_RULES,
    LibraryBottomResultSpec,
)
from ..rules.library_bottom_results import LibraryBottomResultIntent, library_bottom_result_capabilities
from .context import SemanticNodeError
from .intents import IntentPlan
from .zone_move_intent import _freeze_replacement_selections


@dataclass(frozen=True, slots=True)
class LibraryBottomResultHandler:
    handler_id: str = "generic.library-bottom-creature-result.v1"
    schema_version: int = 1
    family: str = "library.move.committed_result"
    operation: str = LIBRARY_BOTTOM_RESULT_OPERATION
    rule_references: tuple[str, ...] = LIBRARY_BOTTOM_RESULT_RULES
    capability_dependencies: tuple[str, ...] = ("zone.change.destination_replacement",)

    def lower(self, effect, context):
        raw = dict(effect)
        selections = raw.pop("_replacement_selections", ())
        try:
            spec = LibraryBottomResultSpec.from_effect(raw, actor=context.actor)
            library_bottom_result_capabilities(spec)
            if context.source is None or not isinstance(selections, (list, tuple)):
                raise ValueError("Library-bottom movement requires a stack source and ordered replacements")
            actor = context.query.require_active_seat(context.actor)
            player = raw["player"]
            if player is None:
                return IntentPlan(self.operation, self.handler_id, ())
            context.query.require_known_seat(player)
            ordered = _freeze_replacement_selections(tuple(selections), family="Library-bottom movement")
            intent = LibraryBottomResultIntent(actor, player, context.source.stack_ref, context.default_reason, spec, ordered)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(self.operation, self.handler_id, (intent,))
