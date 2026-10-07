from __future__ import annotations

"""Instantiate one fixed public query from the source's retained designation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from ..continuous_effects import ContinuousEffect
from ..entry_designations import (
    CHOSEN_CHARACTERISTICS_CAPABILITY,
    CHOSEN_CHARACTERISTICS_HANDLER_ID,
    EntryDesignationKind,
    validate_designation,
)
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError

if TYPE_CHECKING:
    from .continuous_components import ContinuousEffectSourceContext


@dataclass(frozen=True, slots=True)
class ChosenCharacteristicsHandler:
    handler_id: str = CHOSEN_CHARACTERISTICS_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.chosen_designation"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("105.1", "205.3m", "613.1f", "613.1g", "613.4c", "614.12a", "707.2")
    capability_dependencies: tuple[str, ...] = (CHOSEN_CHARACTERISTICS_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]):
        from .continuous_components import (
            FixedQueryCharacteristicGrantHandler,
            FixedQueryKeywordGrantHandler,
            FixedQueryPowerToughnessAnthemHandler,
        )
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "designation", "body"},
            field="chosen characteristics",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError("Chosen characteristic handler identity changed")
        try:
            kind = EntryDesignationKind(descriptor["designation"])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError("Chosen characteristic designation is unsupported") from exc
        body = descriptor["body"]
        if not isinstance(body, Mapping):
            raise SemanticNodeError("Chosen characteristic body must be a closed descriptor")
        handlers = (
            FixedQueryCharacteristicGrantHandler(),
            FixedQueryKeywordGrantHandler(),
            FixedQueryPowerToughnessAnthemHandler(),
        )
        for handler in handlers:
            if body.get("handler_id") == handler.handler_id:
                node = handler.validate(body)
                if (
                    node.predicate.types_all != ("creature",)
                    or node.predicate.colors_all
                    or node.predicate.subtypes_all
                    or node.predicate.subtypes_any
                ):
                    raise SemanticNodeError(
                        "Chosen characteristic body requires one unqualified creature query"
                    )
                return kind, body, handler
        raise SemanticNodeError("Chosen characteristic body owner is unsupported")

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        kind, body, handler = self.validate(descriptor)
        value = context.source_designations.get(kind.annotation)
        if value is None:
            return ()
        try:
            validate_designation(kind, value)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        condition = dict(body["condition"])
        predicate = ObjectQuerySpec.from_dict(condition["predicate"])
        raw = predicate.to_dict()
        raw["colors_all" if kind is EntryDesignationKind.COLOR else "subtypes_all"] = [value]
        condition["predicate"] = ObjectQuerySpec.from_dict(raw).to_dict()
        return handler.lower({**body, "condition": condition}, context)
