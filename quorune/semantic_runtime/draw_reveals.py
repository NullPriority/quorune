from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..drawing.restrictions import drawn_this_turn
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


DRAW_REVEAL_FIRST_HANDLER_ID = "action.draw.reveal-first.v1"
_PLAYER_RELATIONS = {"source_controller"}
_TURN_RELATIONS = {"any", "source_controller_turn"}


class DrawRevealSemantics(Protocol):
    def runtime_handler_programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str,
        event: str,
    ) -> Sequence[Any]: ...


class DrawRevealHost(Protocol):
    semantics: DrawRevealSemantics
    state: Any
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


@dataclass(frozen=True, slots=True)
class DrawRevealSourceContext:
    source_object_id: str
    source_ref: str
    source_logical_object_id: str
    source_zone_change_counter: int
    source_controller: str
    prospective_player: str
    active_player: str | None
    draw_ordinal: int
    component_id: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_object_id", self.source_object_id),
            ("source_ref", self.source_ref),
            ("source_logical_object_id", self.source_logical_object_id),
            ("source_controller", self.source_controller),
            ("prospective_player", self.prospective_player),
            ("component_id", self.component_id),
        ):
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"Draw reveal {field_name} must be a nonempty string"
                )
        if (
            type(self.source_zone_change_counter) is not int
            or self.source_zone_change_counter < 0
        ):
            raise SemanticNodeError(
                "Draw reveal source incarnation must be nonnegative"
            )
        if self.active_player is not None and (
            type(self.active_player) is not str or not self.active_player
        ):
            raise SemanticNodeError(
                "Draw reveal active player must be absent or nonempty"
            )
        if type(self.draw_ordinal) is not int or self.draw_ordinal < 1:
            raise SemanticNodeError(
                "Draw reveal ordinal must be a positive integer"
            )


@dataclass(frozen=True, slots=True)
class DrawRevealPolicy:
    policy_id: str
    source_object_id: str
    source_ref: str
    source_logical_object_id: str
    source_zone_change_counter: int
    source_controller: str
    player: str
    optional: bool

    def __post_init__(self) -> None:
        for field_name, value in (
            ("policy_id", self.policy_id),
            ("source_object_id", self.source_object_id),
            ("source_ref", self.source_ref),
            ("source_logical_object_id", self.source_logical_object_id),
            ("source_controller", self.source_controller),
            ("player", self.player),
        ):
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"Draw reveal policy {field_name} must be nonempty"
                )
        if (
            type(self.source_zone_change_counter) is not int
            or self.source_zone_change_counter < 0
        ):
            raise SemanticNodeError(
                "Draw reveal policy incarnation must be nonnegative"
            )
        if type(self.optional) is not bool:
            raise SemanticNodeError(
                "Draw reveal policy optional flag must be boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "source_object_id": self.source_object_id,
            "source_ref": self.source_ref,
            "source_logical_object_id": self.source_logical_object_id,
            "source_zone_change_counter": self.source_zone_change_counter,
            "source_controller": self.source_controller,
            "player": self.player,
            "optional": self.optional,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DrawRevealPolicy":
        if not isinstance(value, Mapping):
            raise SemanticNodeError("Draw reveal policy must be an object")
        exact_fields(
            value,
            {
                "policy_id",
                "source_object_id",
                "source_ref",
                "source_logical_object_id",
                "source_zone_change_counter",
                "source_controller",
                "player",
                "optional",
            },
            field="draw reveal policy",
        )
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class DrawRevealFirstNode:
    affected_player_relation: str
    turn_relation: str
    draw_ordinal: int
    optional: bool


@dataclass(frozen=True, slots=True)
class DrawRevealFirstHandler:
    handler_id: str = DRAW_REVEAL_FIRST_HANDLER_ID
    schema_version: int = 1
    family: str = "action.draw.reveal_first"
    event: str = "draw.reveal_as_drawn"
    rule_references: tuple[str, ...] = ("121.9",)
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.reveal_as_drawn",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> DrawRevealFirstNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "reveal",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match the draw reveal registry"
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
                "Draw reveal condition must be an object"
            )
        exact_fields(
            condition,
            {
                "affected_player_relation",
                "turn_relation",
                "draw_ordinal",
            },
            field="draw reveal condition",
        )
        relation = condition["affected_player_relation"]
        if relation not in _PLAYER_RELATIONS:
            raise SemanticNodeError(
                "Draw reveal player relation is unsupported"
            )
        turn_relation = condition["turn_relation"]
        if turn_relation not in _TURN_RELATIONS:
            raise SemanticNodeError(
                "Draw reveal turn relation is unsupported"
            )
        ordinal = condition["draw_ordinal"]
        if type(ordinal) is not int or ordinal != 1:
            raise SemanticNodeError(
                "The represented draw reveal policy requires ordinal 1"
            )
        reveal = descriptor["reveal"]
        if not isinstance(reveal, Mapping):
            raise SemanticNodeError("Draw reveal action must be an object")
        exact_fields(
            reveal,
            {"optional", "public"},
            field="draw reveal action",
        )
        if type(reveal["optional"]) is not bool or reveal["public"] is not True:
            raise SemanticNodeError(
                "Draw reveal action requires a boolean optional flag and public reveal"
            )
        return DrawRevealFirstNode(
            affected_player_relation=relation,
            turn_relation=turn_relation,
            draw_ordinal=ordinal,
            optional=reveal["optional"],
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DrawRevealSourceContext,
    ) -> tuple[DrawRevealPolicy, ...]:
        node = self.validate(descriptor)
        if (
            context.prospective_player != context.source_controller
            or context.draw_ordinal != node.draw_ordinal
            or (
                node.turn_relation == "source_controller_turn"
                and context.active_player != context.source_controller
            )
        ):
            return ()
        return (
            DrawRevealPolicy(
                policy_id=(
                    f"{self.handler_id}:{context.source_logical_object_id}:"
                    f"{context.component_id}"
                ),
                source_object_id=context.source_object_id,
                source_ref=context.source_ref,
                source_logical_object_id=context.source_logical_object_id,
                source_zone_change_counter=(
                    context.source_zone_change_counter
                ),
                source_controller=context.source_controller,
                player=context.prospective_player,
                optional=node.optional,
            ),
        )


class DrawRevealRegistry(
    RuntimeComponentRegistry[DrawRevealSourceContext, DrawRevealPolicy]
):
    pass


@lru_cache(maxsize=1)
def default_draw_reveal_registry() -> DrawRevealRegistry:
    registry = DrawRevealRegistry((DrawRevealFirstHandler(),))
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_draw_reveal_policies(
    host: DrawRevealHost,
    player: str,
) -> tuple[DrawRevealPolicy, ...]:
    if type(player) is not str or player not in host.active_seats:
        raise SemanticNodeError(
            "Draw reveal policies require one active prospective player"
        )
    ordinal = drawn_this_turn(host, player) + 1
    registry = default_draw_reveal_registry()
    policies: list[DrawRevealPolicy] = []
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
            event="draw.reveal_as_drawn",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                policies.extend(
                    registry.lower(
                        descriptor,
                        DrawRevealSourceContext(
                            source_object_id=source.object_id,
                            source_ref=source.ref,
                            source_logical_object_id=(
                                source.logical_object_id
                            ),
                            source_zone_change_counter=(
                                source.zone_change_counter
                            ),
                            source_controller=source.controller,
                            prospective_player=player,
                            active_player=host.state.active_player,
                            draw_ordinal=ordinal,
                            component_id=(
                                f"{program.key}:{descriptor_index}"
                            ),
                        ),
                    )
                )
    return tuple(sorted(policies, key=lambda value: value.policy_id))


__all__ = [
    "collect_draw_reveal_policies",
    "default_draw_reveal_registry",
    "DRAW_REVEAL_FIRST_HANDLER_ID",
    "DrawRevealFirstHandler",
    "DrawRevealFirstNode",
    "DrawRevealHost",
    "DrawRevealPolicy",
    "DrawRevealRegistry",
    "DrawRevealSourceContext",
]
