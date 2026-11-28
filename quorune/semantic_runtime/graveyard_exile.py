from __future__ import annotations

"""Qualified destination redirection through the canonical replacement boundary."""

from dataclasses import dataclass, replace
from ..graveyard_exile_model import (
    GraveyardExileSpec,GRAVEYARD_EXILE_HANDLER,GRAVEYARD_EXILE_DEPENDENCIES,
)
from ..replacement.model import ReplaceableEvent,AffectedObject,ReplacementEffect,ReplacementClass
from ..replacement.applicability import condition_matches
from .context import SemanticNodeError
from .zone_replacement_model import ZoneDestinationIntent


@dataclass(frozen=True, slots=True)
class GraveyardExileReplacementHandler:
    handler_id: str = GRAVEYARD_EXILE_HANDLER
    schema_version: int = 1
    family: str = 'replacement.zone.destination'
    event: str = 'zone.change'
    rule_references: tuple[str,...] = ('108.2','110.1','111.8','614.1a','614.4','614.5','614.6','616.1','700.4','730.3e')
    capability_dependencies: tuple[str,...] = GRAVEYARD_EXILE_DEPENDENCIES

    def validate(self,descriptor):
        try:return GraveyardExileSpec.from_descriptor(descriptor)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc

    def source_replacement_effect(self,descriptor,*,source_ref,source_controller,component_id):
        spec=self.validate(descriptor)
        if any(type(value) is not str or not value for value in (source_ref,source_controller,component_id)):
            raise SemanticNodeError('Qualified exile requires its exact current source identity')
        return ReplacementEffect(effect_id=f'{self.handler_id}:{source_ref}:{component_id}',
            source_id=source_ref,event_kind=self.event,replacement_class=ReplacementClass.OTHER,
            conditions=spec.conditions(source_controller),
            operations=({'op':'set','field':'destination','value':'exile'},),
            label=f'{source_ref}: exile the qualifying object instead')

    def replacement_effect(self,descriptor,context):
        return self.source_replacement_effect(descriptor,source_ref=context.source_ref,
            source_controller=context.source_controller,component_id=context.component_id or self.validate(descriptor).scope.value)

    def lower(self,descriptor,context):
        effect=self.replacement_effect(descriptor,context)
        event=ReplaceableEvent(event_id='qualified-exile-probe',kind=self.event,affected_player=None,
            affected_object=AffectedObject(object_id=context.object_id,owner=context.object_owner,controller=context.object_controller),
            payload={'origin':context.origin,'destination':context.destination,
                'object_kind':'card' if context.is_card_object else 'noncard','object_types':list(context.object_types),
                'owner':context.object_owner})
        if not condition_matches(effect.conditions,event):return ()
        return (ZoneDestinationIntent(self.handler_id,context.source_ref,'exile',()),)


from ..rules.zone_replacement_token_trigger import (
    DEPENDENCIES as TOKEN_TRIGGER_DEPENDENCIES, HANDLER as TOKEN_TRIGGER_HANDLER,
    PAYLOAD_FIELD, ReplacementTokenSource, parse_descriptor,
)
from ..replacement.operations import SetField


@dataclass(frozen=True, slots=True)
class GraveyardExileTokenTriggerHandler(GraveyardExileReplacementHandler):
    handler_id: str = TOKEN_TRIGGER_HANDLER
    capability_dependencies: tuple[str, ...] = TOKEN_TRIGGER_DEPENDENCIES
    rule_references: tuple[str, ...] = ("603.3", "603.12", "614.4", "614.5", "614.6", "616.1", "700.4")

    def validate(self, descriptor):
        try:
            return parse_descriptor(descriptor)[0]
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def source_replacement_effect(self, descriptor, *, source_ref, source_controller, component_id, source_context=None):
        if (not isinstance(source_context, ReplacementTokenSource)
                or (source_context.ref, source_context.controller) != (source_ref, source_controller)):
            raise SemanticNodeError("A replacement token trigger needs its original source snapshot")
        base = GraveyardExileReplacementHandler.source_replacement_effect(
            self, descriptor, source_ref=source_ref, source_controller=source_controller, component_id=component_id,
        )
        _, program = parse_descriptor(descriptor)
        return replace(base, operations=(*base.operations,
            SetField(PAYLOAD_FIELD, {"source": source_context.to_dict(), "program": program.to_dict()}),
        ))
