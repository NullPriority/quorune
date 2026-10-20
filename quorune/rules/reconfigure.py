from __future__ import annotations

"""CR 702.151b's consequence of attaching an Equipment with Reconfigure."""

from typing import Any

from ..attachment_duration import AttachmentDuration
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from ..continuous_effect_state import commit_continuous_effect
from ..object_predicate import ObjectQuerySpec


RECONFIGURE_CAPABILITY = 'attachment.reconfigure.fixed_mana'


def source_has_reconfigure(host: Any, source: Any) -> bool:
    return any(ability.reconfigure for ability in host._activated_abilities(source))


def reconfigure_runtime_dependencies(program: Any) -> tuple[str, ...]:
    """A materialized Reconfigure catalog cannot discard its rules authority."""
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
    handler = ActivatedAbilityCatalogHandler()
    for descriptor in program.handlers:
        if descriptor.get('handler_id') == handler.handler_id and descriptor.get('ability', {}).get('reconfigure'):
            if handler.validate(descriptor).reconfigure:
                return (RECONFIGURE_CAPABILITY,)
    return ()


def commit_reconfigure_attachment_effect(host: Any, source: Any, target: Any) -> None:
    """Create the type effect once; later ability loss does not end it."""
    facts = AttachmentDuration(source.object_id, source.logical_object_id,
        target.object_id, target.logical_object_id, source.zone_timestamp)
    effect = ContinuousEffect(
        effect_id=host._next_ref('CE'), source_id=source.object_id,
        layer=Layer.TYPE, sublayer='4', timestamp=source.zone_timestamp,
        operations=(ContinuousOperation('remove_creature_card_type'),),
        origin=ContinuousEffectOrigin.RESOLUTION,
        duration=ContinuousEffectDuration.UNTIL_UNATTACHED,
        applies=ObjectQuerySpec(zones=('battlefield',)),
        locked_objects=(ContinuousObjectIdentity(source.object_id, source.logical_object_id),),
        attachment_duration=facts,
    )
    commit_continuous_effect(host.state, effect)
