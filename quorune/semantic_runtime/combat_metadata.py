from __future__ import annotations

"""Typed runtime metadata for bounded combat rules."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..ability_fragments import canonical_ability_fragments
from ..attachments import attached_object_identity
from ..card_program_faces import program_matches_face
from ..characteristic_evaluation import type_parts
from ..declaration_fragments import ATTACHED_GOAD_CAPABILITY, ATTACHED_GOAD_HANDLER_ID, AttachedGoadSpec
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


GOAD_PROHIBITION_EVENT = "combat.goad.prohibition"
GOAD_PROHIBITION_HANDLER_ID = (
    "prohibition.combat.goad.controller-creatures.v1"
)


@dataclass(frozen=True, slots=True)
class GoadProhibition:
    source_ref: str
    source_controller: str


@dataclass(frozen=True, slots=True)
class GoadProhibitionSourceContext:
    source_ref: str
    source_controller: str


@dataclass(frozen=True, slots=True)
class GoadProhibitionHandler:
    handler_id: str = GOAD_PROHIBITION_HANDLER_ID
    schema_version: int = 1
    family: str = "prohibition.combat.goad"
    event: str = GOAD_PROHIBITION_EVENT
    rule_references: tuple[str, ...] = ("101.2", "604.1", "701.15")
    capability_dependencies: tuple[str, ...] = (
        "combat.goad.prohibition.controller_creatures",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> None:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "affected_controller",
                "affected_card_type",
            },
            field="Goad-prohibition handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Goad-prohibition handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported goad-prohibition schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Goad-prohibition handler must use {self.event}"
            )
        if descriptor["affected_controller"] != "source_controller":
            raise SemanticNodeError(
                "Goad prohibition supports only the source controller"
            )
        if descriptor["affected_card_type"] != "creature":
            raise SemanticNodeError(
                "Goad prohibition supports only creature permanents"
            )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: GoadProhibitionSourceContext,
    ) -> tuple[GoadProhibition, ...]:
        self.validate(descriptor)
        return (
            GoadProhibition(
                source_ref=context.source_ref,
                source_controller=context.source_controller,
            ),
        )


class GoadProhibitionRegistry(
    RuntimeComponentRegistry[GoadProhibitionSourceContext, GoadProhibition]
):
    pass


@dataclass(frozen=True, slots=True)
class AttachedGoadFragmentHandler:
    handler_id: str = ATTACHED_GOAD_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.attached_goad"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("611.3a", "701.15b", "701.15c", "701.15d")
    capability_dependencies: tuple[str, ...] = (ATTACHED_GOAD_CAPABILITY,)

    def validate(self, descriptor):
        from .ability_fragments import _fragment

        return _fragment(descriptor, handler_id=self.handler_id, event=self.event, expected_type=AttachedGoadSpec)

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


@lru_cache(maxsize=1)
def default_goad_prohibition_registry() -> GoadProhibitionRegistry:
    registry = GoadProhibitionRegistry((GoadProhibitionHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


class CombatMetadataHost(Protocol):
    semantics: Any
    state: Any

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> Sequence[Any]: ...

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _goad_prohibition_source(self, card: Any) -> Any: ...

    def _active_goad_designations(self, card: Any) -> Sequence[Any]: ...


def active_goad_prohibitions(
    host: CombatMetadataHost,
) -> tuple[GoadProhibition, ...]:
    """Collect current trusted goad prohibitions without reading prose."""

    registry = default_goad_prohibition_registry()
    prohibitions: list[GoadProhibition] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.zone != "battlefield" or source.phased_out:
            continue
        record = host.card_record(source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone="battlefield",
            event=GOAD_PROHIBITION_EVENT,
        ):
            if (
                not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, source)
            ):
                continue
            context = GoadProhibitionSourceContext(
                source_ref=source.ref,
                source_controller=source.controller,
            )
            for descriptor in program.handlers:
                if registry.describe(str(descriptor.get("handler_id") or "")):
                    prohibitions.extend(registry.lower(descriptor, context))
    return tuple(
        sorted(
            prohibitions,
            key=lambda value: (value.source_ref, value.source_controller),
        )
    )


def active_attached_goaders(host: CombatMetadataHost, card: Any) -> tuple[str, ...]:
    """Read live goad causes from the current reciprocal attachment sources."""

    if card.zone != "battlefield" or card.phased_out or not card.attachments:
        return ()
    types, _, _ = type_parts(str(host._effective_card_data(card).get("type_line") or ""))
    if "creature" not in types:
        return ()
    goaders: set[str] = set()
    for source_id in card.attachments:
        source = host.state.cards.get(source_id)
        if source is None:
            continue
        target = attached_object_identity(host.state.cards, source)
        if target is None or target.object_id != card.object_id:
            continue
        data = host._effective_card_data(source)
        _, subtypes, _ = type_parts(str(data.get("type_line") or ""))
        if any(
            isinstance(fragment, AttachedGoadSpec) and fragment.source_subtype in subtypes
            for fragment in canonical_ability_fragments(data.get("ability_fragments", ()))
        ):
            goaders.add(source.controller)
    if goaders and host._goad_prohibition_source(card) is not None:
        return ()
    return tuple(sorted(goaders))


def active_goading_players(host: CombatMetadataHost, card: Any) -> tuple[str, ...]:
    timed = tuple(designation.player for designation in host._active_goad_designations(card))
    return tuple(dict.fromkeys((*timed, *active_attached_goaders(host, card))))


__all__ = [
    "GOAD_PROHIBITION_EVENT",
    "GOAD_PROHIBITION_HANDLER_ID",
    "GoadProhibition",
    "GoadProhibitionHandler",
    "GoadProhibitionRegistry",
    "AttachedGoadFragmentHandler",
    "active_attached_goaders",
    "active_goading_players",
    "active_goad_prohibitions",
    "default_goad_prohibition_registry",
]
