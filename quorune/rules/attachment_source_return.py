from __future__ import annotations

"""Concrete authority for a battlefield Aura's own return instruction."""

from ..attachment_source_return_model import (
    OPERATION, validate_attachment_source_return, is_attachment_source_return,
)


ATTACHMENT_DEATH = {'field': 'attachments', 'op': 'contains_any', 'value': ['$source.ref']}


def attachment_source_return_node_capabilities(*, effects, target_schema, **unused):
    if len(effects) != 1 or not is_attachment_source_return(effects[0]) or target_schema is not None:
        return ()
    try:
        return validate_attachment_source_return(effects[0], compiled=True).capabilities
    except (TypeError, ValueError, KeyError):
        return ()


def attachment_source_return_runtime_dependencies(program):
    if not any(is_attachment_source_return(effect) for effect in program.effects):
        return ()
    required = attachment_source_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema)
    if (not required or program.event != 'creature.dies' or program.active_zone != 'battlefield'
            or program.event_condition != ATTACHMENT_DEATH or program.cost_schema is not None):
        raise ValueError('Attachment-source return requires its original attached-creature death')
    return required
