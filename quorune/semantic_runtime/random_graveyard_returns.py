from __future__ import annotations

"""Random graveyard selection followed by the canonical simultaneous move port."""

from dataclasses import dataclass
from ..rules.random_graveyard_returns import (OPERATION,CAPABILITY,RandomGraveyardReturnSpec,RandomGraveyardReturnIntent,resolve_random_graveyard_return)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticNodeError
from .intents import IntentPlan,MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True,slots=True)
class RandomGraveyardReturnHandler:
    operation: str=OPERATION
    handler_id: str='generic.random-own-graveyard-return.v1'
    schema_version: int=1
    family: str=CAPABILITY
    rule_references: tuple[str,...]=('101.3','400.7','608.2c','608.2h')
    capability_dependencies: tuple[str,...]=(CAPABILITY,)

    def lower(self,effect,context):
        if (set(effect)-{'op','actor','count','selection','reason','_replacement_selections'}
                or not {'op','actor','count','selection'}<=set(effect) or effect['op']!=OPERATION
                or effect['actor']!=context.actor or context.source is None):
            raise SemanticNodeError('Random graveyard return requires its owned resolving instruction')
        try:intent=RandomGraveyardReturnIntent(context.actor,effect['count'],RandomGraveyardReturnSpec.from_dict(effect['selection']),
            f'{context.source.stack_ref}|{context.source.instruction_pointer}',str(effect.get('reason') or context.default_reason),effect.get('_replacement_selections',()))
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation,handler_id=self.handler_id,intents=(intent,))


def execute_random_graveyard_return(sink,intent):
    def commit(refs):
        return sink.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(actor=intent.actor,
            object_refs=refs,expected_zones=('graveyard',),destination='hand',owned_only=True,
            reason=intent.reason,transition_kind=ZoneTransitionKind.ORDINARY,replacement_selections=intent.replacement_selections))
    return resolve_random_graveyard_return(sink,intent,commit_batch=commit)
