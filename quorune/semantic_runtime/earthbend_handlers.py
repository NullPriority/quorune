from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..earthbend_model import (
    EARTHBEND_CAPABILITY, EARTHBEND_FINISH_OPERATION, EARTHBEND_OPERATION,
    EARTHBEND_RETURN_OPERATION, EARTHBEND_RETURN_ZONES, EarthbendObject,
    earthbend_resolution_values,
)
from ..replacement.immutable import FrozenMap
from .context import SemanticNodeError
from .earthbend_intents import EarthbendIntent, FinishEarthbendIntent, ReturnEarthbentObjectsIntent
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class EarthbendHandler:
    operation: str = EARTHBEND_OPERATION
    handler_id: str = "generic.earthbend-land.v1"
    schema_version: int = 1
    family: str = EARTHBEND_CAPABILITY
    rule_references: tuple[str, ...] = ("701.66a", "608.2c", "613.1d", "613.1f", "613.4b")
    capability_dependencies: tuple[str, ...] = (EARTHBEND_CAPABILITY,)

    def lower(self, effect, context):
        if context.source is None:
            raise SemanticNodeError("Earthbend requires a resolving source")
        try:
            target, amount = earthbend_resolution_values(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        actor = context.query.require_active_seat(context.actor)
        return IntentPlan(
            operation=self.operation, handler_id=self.handler_id,
            intents=(EarthbendIntent(actor, target, amount, context.source),),
        )


@dataclass(frozen=True, slots=True)
class FinishEarthbendHandler:
    operation: str = EARTHBEND_FINISH_OPERATION
    handler_id: str = "generic.finish-earthbend-land.v1"
    schema_version: int = 1
    family: str = EARTHBEND_CAPABILITY
    rule_references: tuple[str, ...] = ("701.66a", "701.66b", "603.7d", "603.7e")
    capability_dependencies: tuple[str, ...] = (EARTHBEND_CAPABILITY,)

    def lower(self, effect, context):
        if (
            set(effect) != {"op", "land", "stack_ref"}
            or effect.get("op") != self.operation
            or context.source is None or effect["stack_ref"] != context.source.stack_ref
        ):
            raise SemanticNodeError("Earthbend continuation requires its resolving stack object")
        try:
            land = EarthbendObject.from_dict(effect["land"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if land.zone != "battlefield":
            raise SemanticNodeError("Earthbend continuation requires the original battlefield incarnation")
        actor = context.query.require_active_seat(context.actor)
        return IntentPlan(
            operation=self.operation, handler_id=self.handler_id,
            intents=(FinishEarthbendIntent(actor, land, context.source),),
        )


@dataclass(frozen=True, slots=True)
class ReturnEarthbentObjectsHandler:
    operation: str = EARTHBEND_RETURN_OPERATION
    handler_id: str = "generic.return-earthbent-objects.v1"
    schema_version: int = 1
    family: str = EARTHBEND_CAPABILITY
    rule_references: tuple[str, ...] = ("701.66a", "400.7e", "603.7c", "730.3c")
    capability_dependencies: tuple[str, ...] = (EARTHBEND_CAPABILITY,)

    def lower(self, effect, context):
        if (
            set(effect) - {"_replacement_selections"} != {"op", "objects"}
            or effect.get("op") != self.operation
            or context.source is None or not isinstance(effect["objects"], (list, tuple))
        ):
            raise SemanticNodeError("Earthbend return requires captured public objects and a resolving source")
        try:
            objects = tuple(EarthbendObject.from_dict(value) for value in effect["objects"])
            if len(objects) != len({value.object_id for value in objects}):
                raise ValueError("Earthbend return objects must be distinct")
            if any(value.zone not in EARTHBEND_RETURN_ZONES for value in objects):
                raise ValueError("Earthbend return requires graveyard or exile objects")
            selections = effect.get("_replacement_selections", ())
            if not isinstance(selections, (list, tuple)) or any(
                not (isinstance(value, str) and value or isinstance(value, Mapping) and value)
                for value in selections
            ):
                raise ValueError("Earthbend return replacement selections are malformed")
            frozen = tuple(FrozenMap(value) if isinstance(value, Mapping) else value for value in selections)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        actor = context.query.require_active_seat(context.actor)
        return IntentPlan(
            operation=self.operation, handler_id=self.handler_id,
            intents=(ReturnEarthbentObjectsIntent(actor, objects, frozen),),
        )


EARTHBEND_HANDLERS = (EarthbendHandler(), FinishEarthbendHandler(), ReturnEarthbentObjectsHandler())
