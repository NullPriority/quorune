from __future__ import annotations

"""Creation-family intent delegation without growing the generic executor."""

from ..incubate_model import IncubateIntent
from .command_object_execution import COMMAND_OBJECT_INTENT_TYPES,execute_command_object_intent


CREATION_INTENT_TYPES = (*COMMAND_OBJECT_INTENT_TYPES,IncubateIntent)


def execute_creation_intent(sink,intent):
    if isinstance(intent,IncubateIntent):
        from ..incubate_execution import execute_incubate_intent
        return intent.controller,execute_incubate_intent(sink,intent)
    return execute_command_object_intent(sink,intent)
