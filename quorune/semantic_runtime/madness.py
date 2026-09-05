from __future__ import annotations

"""Runtime owners for ordinary fixed-mana Madness."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..cast_lifecycles import (
    FixedCastLifecycleError,
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
)
from ..madness import (
    fixed_madness_spec,
    MADNESS_CAPABILITY_ID,
    MADNESS_DISCARD_CAPABILITY_ID,
    MADNESS_REPLACEMENT_EVENT,
    MADNESS_REPLACEMENT_HANDLER_ID,
    MADNESS_TRIGGER_EVENT,
    MADNESS_TRIGGER_HANDLER_ID,
)
from ..replacement.operations import SetField
from ..replacement_effects import (
    ReplacementClass,
    ReplacementEffect,
)
from ..rules.capabilities import load_default_capability_registry
from ..zone_trigger_events import ZoneTransitionKind
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .zone_replacement_model import ZoneChangeSubjectSnapshot


_DESCRIPTOR_FIELDS = {
    "handler_id",
    "schema_version",
    "event",
    REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
    "madness",
}


def _madness_spec(
    descriptor: Mapping[str, Any],
    *,
    handler_id: str,
    event: str,
) -> FixedCastLifecycleSpec:
    exact_fields(descriptor, _DESCRIPTOR_FIELDS, field="Madness handler")
    if (
        descriptor["handler_id"] != handler_id
        or descriptor["schema_version"] != 1
        or descriptor["event"] != event
        or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True
    ):
        raise SemanticNodeError(
            "Madness handler identity, version, event, or admission changed"
        )
    try:
        spec = FixedCastLifecycleSpec.from_dict(descriptor["madness"])
    except (FixedCastLifecycleError, TypeError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    if spec.kind is not FixedCastLifecycleKind.MADNESS:
        raise SemanticNodeError("Madness handlers require a Madness lifecycle")
    if (
        fixed_madness_spec(
            material_line=spec.oracle_line,
            oracle_line=spec.oracle_line,
            line_index=spec.line_index,
        )
        != spec
    ):
        raise SemanticNodeError(
            "Madness handler lifecycle is outside the closed grammar"
        )
    return spec


@dataclass(frozen=True, slots=True)
class MadnessDiscardReplacementHandler:
    handler_id: str = MADNESS_REPLACEMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.madness_discard"
    event: str = MADNESS_REPLACEMENT_EVENT
    rule_references: tuple[str, ...] = ("400.7k", "702.35a")
    capability_dependencies: tuple[str, ...] = (
        MADNESS_CAPABILITY_ID,
        MADNESS_DISCARD_CAPABILITY_ID,
        "zone.change.destination_replacement",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> FixedCastLifecycleSpec:
        return _madness_spec(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedCastLifecycleSpec, ...]:
        del context
        return (self.validate(descriptor),)

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> ReplacementEffect | None:
        self.validate(descriptor)
        if (
            subject.origin != "hand"
            or subject.destination != "graveyard"
            or subject.transition_kind is not ZoneTransitionKind.DISCARD
            or not subject.is_card_object
        ):
            return None
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{subject.logical_object_id}:{component_id}"
            ),
            source_id=subject.object_ref,
            event_kind="zone.change",
            replacement_class=ReplacementClass.SELF_REPLACEMENT,
            conditions={
                "origin": {"eq": "hand"},
                "destination": {"eq": "graveyard"},
                "transition_kind": {"eq": ZoneTransitionKind.DISCARD.value},
                "object_ref": {"eq": subject.object_ref},
                "logical_object_id": {"eq": subject.logical_object_id},
            },
            operations=(SetField("destination", "exile"),),
            label=f"{subject.object_ref}: discard into exile for Madness",
        )


@dataclass(frozen=True, slots=True)
class MadnessTriggerHandler:
    handler_id: str = MADNESS_TRIGGER_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.madness.trigger"
    event: str = MADNESS_TRIGGER_EVENT
    rule_references: tuple[str, ...] = ("400.7k", "702.35a", "702.35b")
    capability_dependencies: tuple[str, ...] = (MADNESS_CAPABILITY_ID,)

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> FixedCastLifecycleSpec:
        return _madness_spec(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedCastLifecycleSpec, ...]:
        del context
        return (self.validate(descriptor),)


class MadnessTriggerRegistry(
    RuntimeComponentRegistry[object, FixedCastLifecycleSpec]
):
    pass


@lru_cache(maxsize=1)
def default_madness_trigger_registry() -> MadnessTriggerRegistry:
    registry = MadnessTriggerRegistry((MadnessTriggerHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


__all__ = [
    "default_madness_trigger_registry",
    "MadnessDiscardReplacementHandler",
    "MadnessTriggerHandler",
    "MadnessTriggerRegistry",
]
