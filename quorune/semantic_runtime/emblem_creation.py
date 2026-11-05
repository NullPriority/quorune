from __future__ import annotations

from dataclasses import dataclass

from ..emblem_program_model import CompiledEmblemSpec, EMBLEM_PROGRAM_CAPABILITY
from .context import SemanticNodeError
from .intents import IntentPlan
from .creation_intents import CreateCompiledEmblemIntent


@dataclass(frozen=True, slots=True)
class CreateCompiledEmblemHandler:
    handler_id: str = 'generic.create-compiled-emblem.v1'
    operation: str = 'create_compiled_emblem'
    family: str = 'effect.emblem-creation'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('114.1', '114.2', '114.3', '114.4', '114.5')
    capability_dependencies: tuple[str, ...] = ('object.emblem.creation', EMBLEM_PROGRAM_CAPABILITY)

    def lower(self, effect, context):
        if (set(effect) != {'op', 'controller', 'emblem'} or effect.get('op') != self.operation
                or effect.get('controller') not in {'$controller', context.actor}):
            raise SemanticNodeError('Compiled emblem creation has a closed controller-bound schema')
        try:
            spec = CompiledEmblemSpec.from_dict(effect['emblem'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        context.query.require_active_seat(context.actor)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id,
            intents=(CreateCompiledEmblemIntent(context.actor, spec, context.default_reason),))
