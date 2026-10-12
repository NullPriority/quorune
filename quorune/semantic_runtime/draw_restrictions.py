from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..drawing.restrictions import (
    DrawPermission,
    DrawRestriction,
    drawn_this_turn,
    evaluate_draw_permission,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


DRAW_MAXIMUM_HANDLER_ID = "restriction.draw.maximum-per-turn.v1"
_RELATIONS = {"any", "opponent", "source_controller"}


class DrawRestrictionSemantics(Protocol):
    def runtime_handler_programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str,
        event: str,
    ) -> Sequence[Any]: ...


class DrawRestrictionHost(Protocol):
    semantics: DrawRestrictionSemantics
    state: Any
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


@dataclass(frozen=True, slots=True)
class DrawRestrictionSourceContext:
    source_ref: str
    source_controller: str
    prospective_player: str
    component_id: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_ref", self.source_ref),
            ("source_controller", self.source_controller),
            ("prospective_player", self.prospective_player),
            ("component_id", self.component_id),
        ):
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"Draw restriction {field_name} must be a nonempty string"
                )


@dataclass(frozen=True, slots=True)
class DrawMaximumNode:
    affected_player_relation: str
    maximum_per_turn: int


def _relation(value: Any) -> str:
    if type(value) is not str or value not in _RELATIONS:
        raise SemanticNodeError(
            "Draw restriction relation must be any, opponent, or source_controller"
        )
    return value


def _applies(node: DrawMaximumNode, context: DrawRestrictionSourceContext) -> bool:
    if node.affected_player_relation == "any":
        return True
    if node.affected_player_relation == "source_controller":
        return context.prospective_player == context.source_controller
    return context.prospective_player != context.source_controller


@dataclass(frozen=True, slots=True)
class DrawMaximumHandler:
    handler_id: str = DRAW_MAXIMUM_HANDLER_ID
    schema_version: int = 1
    family: str = "restriction.draw.maximum_per_turn"
    event: str = "draw.permission"
    rule_references: tuple[str, ...] = (
        "121.2b",
        "121.3",
        "121.3a",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> DrawMaximumNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "restriction",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match the draw restriction registry"
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
                "Draw restriction condition must be an object"
            )
        exact_fields(
            condition,
            {"affected_player_relation"},
            field="draw restriction condition",
        )
        restriction = descriptor["restriction"]
        if not isinstance(restriction, Mapping):
            raise SemanticNodeError(
                "Draw restriction value must be an object"
            )
        exact_fields(
            restriction,
            {"maximum_per_turn"},
            field="draw restriction value",
        )
        maximum = restriction["maximum_per_turn"]
        if type(maximum) is not int or maximum not in {0, 1}:
            raise SemanticNodeError(
                "Represented draw maximum must be the integer 0 or 1"
            )
        return DrawMaximumNode(
            affected_player_relation=_relation(
                condition["affected_player_relation"]
            ),
            maximum_per_turn=maximum,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DrawRestrictionSourceContext,
    ) -> tuple[DrawRestriction, ...]:
        node = self.validate(descriptor)
        if not _applies(node, context):
            return ()
        return (
            DrawRestriction(
                restriction_id=(
                    f"{self.handler_id}:{context.source_ref}:"
                    f"{context.component_id}"
                ),
                source_ref=context.source_ref,
                maximum_per_turn=node.maximum_per_turn,
            ),
        )


class DrawRestrictionRegistry(
    RuntimeComponentRegistry[DrawRestrictionSourceContext, DrawRestriction]
):
    pass


@lru_cache(maxsize=1)
def default_draw_restriction_registry() -> DrawRestrictionRegistry:
    registry = DrawRestrictionRegistry((DrawMaximumHandler(),))
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_draw_restrictions(
    host: DrawRestrictionHost,
    player: str,
) -> tuple[DrawRestriction, ...]:
    if type(player) is not str or player not in host.active_seats:
        raise SemanticNodeError(
            "Draw restrictions require one active prospective player"
        )
    registry = default_draw_restriction_registry()
    restrictions: list[DrawRestriction] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if (
            source.zone != "battlefield"
            or source.phased_out
            or source.controller not in host.active_seats
        ):
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(source),
            active_zone="battlefield",
            event="draw.permission",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                restrictions.extend(
                    registry.lower(
                        descriptor,
                        DrawRestrictionSourceContext(
                            source_ref=source.ref,
                            source_controller=source.controller,
                            prospective_player=player,
                            component_id=f"{program.key}:{descriptor_index}",
                        ),
                    )
                )
    return tuple(sorted(restrictions, key=lambda value: value.restriction_id))


def current_draw_permission(
    host: DrawRestrictionHost,
    player: str,
) -> DrawPermission:
    try:
        restrictions = collect_draw_restrictions(host, player)
        count = drawn_this_turn(host, player)
    except (SemanticNodeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return evaluate_draw_permission(
        player,
        drawn_this_turn=count,
        restrictions=restrictions,
    )


__all__ = [
    "collect_draw_restrictions",
    "current_draw_permission",
    "default_draw_restriction_registry",
    "DRAW_MAXIMUM_HANDLER_ID",
    "DrawMaximumHandler",
    "DrawMaximumNode",
    "DrawRestrictionHost",
    "DrawRestrictionRegistry",
    "DrawRestrictionSourceContext",
]
