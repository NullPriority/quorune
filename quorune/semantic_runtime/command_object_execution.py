from __future__ import annotations

"""Delegation to typed command-object creation and retained Ring owners."""

from .creation_intents import CreateCompiledEmblemIntent
from .ring_execution import RING_INTENT_TYPES, execute_ring_intent

COMMAND_OBJECT_INTENT_TYPES = (*RING_INTENT_TYPES, CreateCompiledEmblemIntent)


def execute_command_object_intent(sink, intent):
    if isinstance(intent, CreateCompiledEmblemIntent):
        from ..emblems import create_compiled_emblem
        return intent.player, create_compiled_emblem(sink, intent.player, intent.emblem, reason=intent.reason)
    return execute_ring_intent(sink, intent)
