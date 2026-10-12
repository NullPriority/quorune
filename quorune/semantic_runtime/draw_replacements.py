from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..replacement import (
    CreateResultDraws,
    DredgeDraw,
    MultiplyAmount,
    ReplacementClass,
    ReplacementEffect,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


DREDGE_HANDLER_ID = "replacement.draw.dredge.v1"
DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID = (
    "replacement.draw.instruction.multiply.v1"
)
DRAW_RESULT_MULTIPLIER_HANDLER_ID = "replacement.draw.result.multiply.v1"
_DREDGE_LABEL = "Dredge "


class DrawReplacementSemantics(Protocol):
    def runtime_handler_programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str,
        event: str,
    ) -> Sequence[Any]: ...


class DrawReplacementCard(Protocol):
    owner: str
    controller: str
    zone: str
    phased_out: bool
    oracle_id: str
    ref: str
    object_id: str
    zone_change_counter: int


class DrawReplacementPlayer(Protocol):
    zones: Mapping[str, list[str]]


class DrawReplacementState(Protocol):
    players: Mapping[str, DrawReplacementPlayer]
    cards: Mapping[str, DrawReplacementCard]


class DrawReplacementHost(Protocol):
    semantics: DrawReplacementSemantics
    state: DrawReplacementState
    active_seats: list[str]

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class DrawReplacementSourceContext:
    source_ref: str
    source_object_id: str
    source_zone_change_counter: int
    source_owner: str
    source_controller: str = ""
    component_id: str = ""

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_ref", self.source_ref),
            ("source_object_id", self.source_object_id),
            ("source_owner", self.source_owner),
        ):
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"{_DREDGE_LABEL}{field_name} must be a nonempty string"
                )
        if (
            type(self.source_zone_change_counter) is not int
            or self.source_zone_change_counter < 0
        ):
            raise SemanticNodeError(
                "Dredge source zone-change counter must be nonnegative"
            )
        if self.source_controller and type(self.source_controller) is not str:
            raise SemanticNodeError(
                "Draw replacement source controller must be a string"
            )


@dataclass(frozen=True, slots=True)
class DrawInstructionReplacementSourceContext:
    source_ref: str
    source_object_id: str
    source_zone_change_counter: int
    source_controller: str
    component_id: str = ""

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_ref", self.source_ref),
            ("source_object_id", self.source_object_id),
            ("source_controller", self.source_controller),
        ):
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"Draw instruction {field_name} must be a nonempty string"
                )
        if (
            type(self.source_zone_change_counter) is not int
            or self.source_zone_change_counter < 0
        ):
            raise SemanticNodeError(
                "Draw instruction source zone-change counter must be nonnegative"
            )


@dataclass(frozen=True, slots=True)
class DrawResultMultiplierNode:
    affected_player_relation: str
    factor: int


@dataclass(frozen=True, slots=True)
class DrawResultMultiplierHandler:
    """Closed CR 121.7 replacement producing one new draw instruction."""

    handler_id: str = DRAW_RESULT_MULTIPLIER_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.draw.result_quantity"
    event: str = "draw"
    rule_references: tuple[str, ...] = (
        "121.6",
        "121.6b",
        "121.7",
        "614.5",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.result_generated_ordering",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DrawResultMultiplierNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "modification",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match the draw replacement registry"
            )
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(f"{self.handler_id} must handle draw")
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError("Draw result condition must be an object")
        exact_fields(
            condition,
            {"affected_player_relation"},
            field="draw result condition",
        )
        if condition["affected_player_relation"] != "source_controller":
            raise SemanticNodeError(
                "Represented result draws require the source controller"
            )
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Draw result modification must be an object"
            )
        exact_fields(
            modification,
            {"factor"},
            field="draw result modification",
        )
        factor = modification["factor"]
        if type(factor) is not int or factor != 2:
            raise SemanticNodeError(
                "Represented result draw factor must be the integer 2"
            )
        return DrawResultMultiplierNode(
            affected_player_relation="source_controller",
            factor=factor,
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        if not context.source_controller:
            raise SemanticNodeError(
                "Draw result replacement requires a source controller"
            )
        component_id = context.component_id or str(node.factor)
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_object_id}@"
                f"{context.source_zone_change_counter}:{component_id}"
            ),
            source_id=(
                f"{context.source_object_id}@"
                f"{context.source_zone_change_counter}"
            ),
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "affected_player": {"eq": context.source_controller},
                "is_draw": {"eq": True},
            },
            operations=(CreateResultDraws(count=node.factor),),
            label=(
                f"{context.source_ref}: draw {node.factor} cards instead"
            ),
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


@dataclass(frozen=True, slots=True)
class DrawInstructionMultiplierNode:
    affected_player_relation: str
    factor: int


@dataclass(frozen=True, slots=True)
class DrawInstructionMultiplierHandler:
    """Closed CR 121.2a count replacement for controller draw doubling."""

    handler_id: str = DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.draw.instruction_quantity"
    event: str = "draw.instruction"
    rule_references: tuple[str, ...] = (
        "121.2",
        "121.2a",
        "616.1g",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DrawInstructionMultiplierNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "modification",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match the draw replacement registry"
            )
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"{self.handler_id} must handle {self.event}"
            )
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError(
                "Draw instruction condition must be an object"
            )
        exact_fields(
            condition,
            {"affected_player_relation"},
            field="draw instruction condition",
        )
        if condition["affected_player_relation"] != "source_controller":
            raise SemanticNodeError(
                "Represented draw instruction multiplication requires the source controller"
            )
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Draw instruction modification must be an object"
            )
        exact_fields(
            modification,
            {"factor"},
            field="draw instruction modification",
        )
        factor = modification["factor"]
        if type(factor) is not int or factor != 2:
            raise SemanticNodeError(
                "Represented draw instruction factor must be the integer 2"
            )
        return DrawInstructionMultiplierNode(
            affected_player_relation="source_controller",
            factor=factor,
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DrawInstructionReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        component_id = context.component_id or str(node.factor)
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_object_id}@"
                f"{context.source_zone_change_counter}:{component_id}"
            ),
            source_id=(
                f"{context.source_object_id}@"
                f"{context.source_zone_change_counter}"
            ),
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "affected_player": {"eq": context.source_controller},
                "count": {"gt": 0},
            },
            operations=(MultiplyAmount(field="count", factor=node.factor),),
            label=f"{context.source_ref}: draw twice that many cards",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DrawInstructionReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


@dataclass(frozen=True, slots=True)
class DredgeReplacementNode:
    mill_count: int


@dataclass(frozen=True, slots=True)
class DredgeReplacementHandler:
    handler_id: str = DREDGE_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.draw.dredge"
    event: str = "draw"
    rule_references: tuple[str, ...] = (
        "121.6",
        "121.6a",
        "121.6b",
        "702.52a",
        "702.52b",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> DredgeReplacementNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "modification",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match the Dredge registry"
            )
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"{self.handler_id} must handle {self.event}"
            )
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Dredge replacement modification must be an object"
            )
        exact_fields(
            modification,
            {"mill_count"},
            field="Dredge replacement modification",
        )
        mill_count = modification["mill_count"]
        if type(mill_count) is not int or mill_count < 1:
            raise SemanticNodeError(
                "Dredge mill count must be a positive integer"
            )
        return DredgeReplacementNode(mill_count=mill_count)

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        component_id = context.component_id or str(node.mill_count)
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_object_id}@"
                f"{context.source_zone_change_counter}:{component_id}"
            ),
            source_id=(
                f"{context.source_object_id}@"
                f"{context.source_zone_change_counter}"
            ),
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "affected_player": {"eq": context.source_owner},
                "is_draw": {"eq": True},
                "library_size": {"gte": node.mill_count},
            },
            operations=(
                DredgeDraw(
                    source_ref=context.source_ref,
                    source_object_id=context.source_object_id,
                    source_zone_change_counter=(
                        context.source_zone_change_counter
                    ),
                    mill_count=node.mill_count,
                ),
            ),
            optional=True,
            label=f"{_DREDGE_LABEL}{node.mill_count} — {context.source_ref}",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


class DrawReplacementRegistry(
    RuntimeComponentRegistry[
        DrawReplacementSourceContext,
        ReplacementEffect,
    ]
):
    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> ReplacementEffect:
        handler = self._handler(descriptor)
        compiler = getattr(handler, "replacement_effect", None)
        if compiler is None:
            raise SemanticNodeError(
                f"Runtime handler {handler.handler_id} cannot compile a "
                "draw replacement"
            )
        return compiler(descriptor, context)


@lru_cache(maxsize=1)
def default_draw_replacement_registry() -> DrawReplacementRegistry:
    registry = DrawReplacementRegistry(
        (
            DredgeReplacementHandler(),
            DrawInstructionMultiplierHandler(),
            DrawResultMultiplierHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_draw_replacement_effects(
    host: DrawReplacementHost,
    player: str,
) -> tuple[ReplacementEffect, ...]:
    """Compile trusted graveyard draw replacements for one affected player."""

    if type(player) is not str or player not in host.active_seats:
        raise SemanticNodeError(
            "Draw replacements require one active affected player"
        )
    registry = default_draw_replacement_registry()
    graveyard = host.state.players[player].zones["graveyard"]
    effects: list[ReplacementEffect] = []
    for object_id in graveyard:
        source = host.state.cards[object_id]
        if source.owner != player or source.zone != "graveyard":
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(source),
            active_zone="graveyard",
            event="draw",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                effects.append(
                    registry.replacement_effect(
                        descriptor,
                        DrawReplacementSourceContext(
                            source_ref=source.ref,
                            source_object_id=source.object_id,
                            source_zone_change_counter=(
                                source.zone_change_counter
                            ),
                            source_owner=source.owner,
                            source_controller=source.controller,
                            component_id=(
                                f"{program.key}:{descriptor_index}"
                            ),
                        ),
                    )
                )
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if (
            source.zone != "battlefield"
            or source.phased_out
            or source.controller != player
        ):
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(source),
            active_zone="battlefield",
            event="draw",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                effects.append(
                    registry.replacement_effect(
                        descriptor,
                        DrawReplacementSourceContext(
                            source_ref=source.ref,
                            source_object_id=source.object_id,
                            source_zone_change_counter=(
                                source.zone_change_counter
                            ),
                            source_owner=source.owner,
                            source_controller=source.controller,
                            component_id=(
                                f"{program.key}:{descriptor_index}"
                            ),
                        ),
                    )
                )
    return tuple(sorted(effects, key=lambda value: value.effect_id))


def current_dredge_operation(
    host: DrawReplacementHost,
    player: str,
    source_ref: str,
) -> DredgeDraw | None:
    """Return one live trusted Dredge operation for a legacy source choice."""

    if type(source_ref) is not str or not source_ref:
        raise SemanticNodeError("Dredge source reference must be nonempty")
    operations = tuple(
        operation
        for effect in collect_draw_replacement_effects(host, player)
        for operation in effect.operations
        if isinstance(operation, DredgeDraw)
        and operation.source_ref == source_ref
    )
    if len(operations) > 1:
        raise SemanticNodeError(
            "A Dredge source cannot provide multiple current operations"
        )
    return operations[0] if operations else None


def collect_draw_instruction_replacement_effects(
    host: DrawReplacementHost,
    player: str,
) -> tuple[ReplacementEffect, ...]:
    """Compile trusted battlefield replacements for a draw-count instruction."""

    if type(player) is not str or player not in host.active_seats:
        raise SemanticNodeError(
            "Draw instruction replacements require one active affected player"
        )
    registry = default_draw_replacement_registry()
    effects: list[ReplacementEffect] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if (
            source.zone != "battlefield"
            or source.phased_out
            or source.controller != player
        ):
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(source),
            active_zone="battlefield",
            event="draw.instruction",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                effects.append(
                    registry.replacement_effect(
                        descriptor,
                        DrawInstructionReplacementSourceContext(
                            source_ref=source.ref,
                            source_object_id=source.object_id,
                            source_zone_change_counter=(
                                source.zone_change_counter
                            ),
                            source_controller=source.controller,
                            component_id=f"{program.key}:{descriptor_index}",
                        ),
                    )
                )
    return tuple(sorted(effects, key=lambda value: value.effect_id))


__all__ = [
    "collect_draw_instruction_replacement_effects",
    "collect_draw_replacement_effects",
    "current_dredge_operation",
    "default_draw_replacement_registry",
    "DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID",
    "DRAW_RESULT_MULTIPLIER_HANDLER_ID",
    "DrawInstructionMultiplierHandler",
    "DrawInstructionMultiplierNode",
    "DrawResultMultiplierHandler",
    "DrawResultMultiplierNode",
    "DrawInstructionReplacementSourceContext",
    "DREDGE_HANDLER_ID",
    "DredgeReplacementHandler",
    "DredgeReplacementNode",
    "DrawReplacementHost",
    "DrawReplacementRegistry",
    "DrawReplacementSourceContext",
]
