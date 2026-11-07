from __future__ import annotations

"""Creation-family intent delegation without growing the generic executor."""

from ..incubate_model import IncubateIntent
from ..replacement.immutable import thaw_value
from .context import SemanticNodeError
from .token_creation_intent import DistributedTokenCreationIntent
from .command_object_execution import COMMAND_OBJECT_INTENT_TYPES,execute_command_object_intent


CREATION_INTENT_TYPES = (*COMMAND_OBJECT_INTENT_TYPES, IncubateIntent, DistributedTokenCreationIntent)


def execute_creation_intent(sink,intent):
    if isinstance(intent,IncubateIntent):
        from ..incubate_execution import execute_incubate_intent
        return intent.controller,execute_incubate_intent(sink,intent)
    if isinstance(intent, DistributedTokenCreationIntent):
        from ..token_creation import create_distributed_tokens

        item = sink.state.stack[-1] if sink.state.stack else None
        if (item is None or item.ref != intent.stack_ref or item.controller != intent.actor
                or item.context.get("currently_resolving") is not True):
            raise SemanticNodeError("Distributed tokens require their current resolving stack item")
        return intent.actor, create_distributed_tokens(
            sink, intent.creators, token=thaw_value(intent.token),
            stack_ref=f"{intent.stack_ref}:{intent.instruction_pointer}",
            reason=intent.reason,
            replacement_selections=thaw_value(intent.replacement_selections),
        )
    return execute_command_object_intent(sink,intent)
