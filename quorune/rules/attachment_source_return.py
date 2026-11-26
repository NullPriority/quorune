from __future__ import annotations

"""Concrete authority for a battlefield Aura's own return instruction."""

from ..attachment_source_return_model import (
    AttachmentSourceReturnSpec, OPERATION, validate_attachment_source_return, is_attachment_source_return,
)
from ..event_card_return import EVENT_BATTLEFIELD_RETURN_MECHANIC, event_battlefield_return_effect
from ..replacement.immutable import thaw_value


ATTACHMENT_DEATH = {'field': 'attachments', 'op': 'contains_any', 'value': ['$source.ref']}
ATTACHMENT_DEATH_TRANSFORM_CAPABILITY = "zone.return.attachment_death_and_transform"
ATTACHMENT_DEATH_TRANSFORM_MECHANIC = "attachment-death-return-and-transform"


def attachment_death_transform_effects():
    return (
        event_battlefield_return_effect(tapped=False, controller="$controller"),
        AttachmentSourceReturnSpec("any", "immediate", transformed=True).effect(),
    )


def attachment_death_transform_capabilities(effects):
    from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities

    if thaw_value(effects) != thaw_value(attachment_death_transform_effects()):
        return ()
    creature = fixed_target_reanimation_node_capabilities(
        effects=effects[:1], target_schema=None, mechanic_ids={EVENT_BATTLEFIELD_RETURN_MECHANIC},
    )
    if not creature:
        return ()
    aura = validate_attachment_source_return(effects[1], compiled=True).capabilities
    return tuple(dict.fromkeys((ATTACHMENT_DEATH_TRANSFORM_CAPABILITY, *creature, *aura)))


def attachment_source_return_node_capabilities(*, effects, target_schema, mechanic_ids=(), **unused):
    if ATTACHMENT_DEATH_TRANSFORM_MECHANIC in mechanic_ids:
        if target_schema is not None:
            return ()
        try:
            return attachment_death_transform_capabilities(effects)
        except (TypeError, ValueError, KeyError):
            return ()
    if len(effects) != 1 or not is_attachment_source_return(effects[0]) or target_schema is not None:
        return ()
    try:
        return validate_attachment_source_return(effects[0], compiled=True).capabilities
    except (TypeError, ValueError, KeyError):
        return ()


def attachment_source_return_runtime_dependencies(program):
    mechanics = getattr(program, "coverage", ())
    compound = ATTACHMENT_DEATH_TRANSFORM_MECHANIC in mechanics
    if not compound and not any(is_attachment_source_return(effect) for effect in program.effects):
        return ()
    required = attachment_source_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=mechanics)
    if (not required or program.event != 'creature.dies' or program.active_zone != 'battlefield'
            or program.event_condition != ATTACHMENT_DEATH or program.cost_schema is not None):
        raise ValueError('Attachment-source return requires its original attached-creature death')
    if compound and (program.handlers or program.trigger_limit is not None):
        raise ValueError("Attachment death and transformation require their whole original trigger")
    return required
