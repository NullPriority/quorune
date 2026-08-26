from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..morph import (
    DISGUISE_CAST_METHOD,
    FACE_DOWN_CAST_METHODS,
    FACE_DOWN_METHOD_CAPABILITY_IDS,
    FACE_DOWN_METHOD_HANDLER_IDS,
    FACE_DOWN_METHOD_RUNTIME_EVENTS,
    FixedManaMorphSpec,
    MEGAMORPH_CAST_METHOD,
    MORPH_CAPABILITY_ID,
    MORPH_CAST_METHOD,
    MORPH_HANDLER_ID,
    MORPH_RUNTIME_EVENT,
    MorphError,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedManaMorphHandler:
    method: str = MORPH_CAST_METHOD
    handler_id: str = MORPH_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.morph.fixed_mana"
    event: str = MORPH_RUNTIME_EVENT
    rule_references: tuple[str, ...] = (
        "116.2b",
        "702.37",
        "702.37a",
        "702.37c",
        "702.37e",
        "708.2",
        "708.4",
        "708.5",
        "708.8",
        "708.9",
    )
    capability_dependencies: tuple[str, ...] = (MORPH_CAPABILITY_ID,)

    def __post_init__(self) -> None:
        if self.method not in FACE_DOWN_CAST_METHODS:
            raise ValueError("Unsupported face-down runtime method")
        if self.handler_id != FACE_DOWN_METHOD_HANDLER_IDS[self.method]:
            raise ValueError("Face-down runtime handler ID mismatch")
        if self.event != FACE_DOWN_METHOD_RUNTIME_EVENTS[self.method]:
            raise ValueError("Face-down runtime event mismatch")
        if self.capability_dependencies != (
            FACE_DOWN_METHOD_CAPABILITY_IDS[self.method],
        ):
            raise ValueError("Face-down runtime capability mismatch")

    def validate(self, descriptor: Mapping[str, Any]) -> FixedManaMorphSpec:
        payload_field = (
            "morph"
            if self.method == MORPH_CAST_METHOD
            else "face_down_method"
        )
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                payload_field,
            },
            field="fixed-mana face-down method handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Face-down method handler ID mismatch")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported face-down method handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError("Face-down method handler event mismatch")
        if descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError(
                "Face-down method requires complete-card program admission"
            )
        value = descriptor[payload_field]
        if not isinstance(value, Mapping):
            raise SemanticNodeError(
                "Face-down method descriptor must be an object"
            )
        try:
            spec = FixedManaMorphSpec.from_dict(value)
        except MorphError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if spec.method != self.method:
            raise SemanticNodeError("Face-down method descriptor mismatch")
        return spec

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedManaMorphSpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedManaMorphRegistry(RuntimeComponentRegistry[object, FixedManaMorphSpec]):
    pass


@lru_cache(maxsize=1)
def default_fixed_mana_morph_registry() -> FixedManaMorphRegistry:
    handlers = [FixedManaMorphHandler()]
    for method, rule_references in (
        (
            MEGAMORPH_CAST_METHOD,
            ("116.2b", "702.37", "702.37c", "702.37e", "708"),
        ),
        (
            DISGUISE_CAST_METHOD,
            ("116.2b", "702.168", "702.168a", "702.168b", "708"),
        ),
    ):
        handlers.append(
            FixedManaMorphHandler(
                method=method,
                handler_id=FACE_DOWN_METHOD_HANDLER_IDS[method],
                family=FACE_DOWN_METHOD_CAPABILITY_IDS[method],
                event=FACE_DOWN_METHOD_RUNTIME_EVENTS[method],
                rule_references=rule_references,
                capability_dependencies=(
                    FACE_DOWN_METHOD_CAPABILITY_IDS[method],
                ),
            )
        )
    registry = FixedManaMorphRegistry(tuple(handlers))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


__all__ = [
    "default_fixed_mana_morph_registry",
    "FixedManaMorphHandler",
    "FixedManaMorphRegistry",
]
