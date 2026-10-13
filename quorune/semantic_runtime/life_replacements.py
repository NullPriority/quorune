from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..replacement_effects import ReplacementClass, ReplacementEffect
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


_LIFE_GAIN_HANDLER_ID = "replacement.life.gain.multiplier.v1"
_RELATIONS = {"any", "source_controller", "opponent"}


class LifeReplacementSemantics(Protocol):
    def runtime_handler_programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str,
        event: str,
    ) -> Sequence[Any]: ...


class LifeReplacementHost(Protocol):
    semantics: LifeReplacementSemantics
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


@dataclass(frozen=True, slots=True)
class LifeReplacementSourceContext:
    source_ref: str
    source_controller: str
    component_id: str = ""

    def __post_init__(self) -> None:
        if not self.source_ref or not self.source_controller:
            raise SemanticNodeError(
                "Life replacements require a source and controller"
            )


@dataclass(frozen=True, slots=True)
class LifeGainMultiplierNode:
    affected_player_relation: str
    multiplier: int


def _relation_condition(
    relation: str,
    source_controller: str,
) -> Mapping[str, Any] | None:
    if relation == "any":
        return None
    if relation == "source_controller":
        return {"eq": source_controller}
    return {"not_in": [source_controller, None]}


def _relation(value: Any, *, field: str) -> str:
    relation = str(value)
    if relation not in _RELATIONS:
        raise SemanticNodeError(
            f"{field} must be any, source_controller, or opponent"
        )
    return relation


@dataclass(frozen=True, slots=True)
class LifeGainMultiplierHandler:
    handler_id: str = _LIFE_GAIN_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.life.gain.multiplier"
    event: str = "life.change"
    rule_references: tuple[str, ...] = (
        "119.3",
        "119.10",
        "614.1",
        "616.1",
        "616.1f",
        "616.1g",
    )
    capability_dependencies: tuple[str, ...] = (
        "life.gain.replacement.static_multiplier",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> LifeGainMultiplierNode:
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
                "Runtime handler ID does not match registry"
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
                "Life-gain replacement condition must be an object"
            )
        exact_fields(
            condition,
            {"affected_player_relation"},
            field="life-gain replacement condition",
        )
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Life-gain replacement modification must be an object"
            )
        exact_fields(
            modification,
            {"multiplier"},
            field="life-gain replacement modification",
        )
        multiplier = modification["multiplier"]
        if type(multiplier) is not int or multiplier < 2:
            raise SemanticNodeError(
                "Life-gain multiplier must be an integer of at least 2"
            )
        return LifeGainMultiplierNode(
            affected_player_relation=_relation(
                condition["affected_player_relation"],
                field="condition.affected_player_relation",
            ),
            multiplier=multiplier,
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: LifeReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        conditions: dict[str, Any] = {
            "direction": {"eq": "gain"},
            "amount": {"not_in": [0]},
        }
        affected = _relation_condition(
            node.affected_player_relation,
            context.source_controller,
        )
        if affected is not None:
            conditions["affected_player"] = affected
        component_id = context.component_id or str(node.multiplier)
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:{component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions=conditions,
            operations=(
                {
                    "op": "multiply",
                    "field": "amount",
                    "factor": node.multiplier,
                },
            ),
            label=f"{context.source_ref}: multiply life gained",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: LifeReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


class LifeReplacementRegistry(
    RuntimeComponentRegistry[
        LifeReplacementSourceContext,
        ReplacementEffect,
    ]
):
    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: LifeReplacementSourceContext,
    ) -> ReplacementEffect:
        handler = self._handler(descriptor)
        compiler = getattr(handler, "replacement_effect", None)
        if compiler is None:
            raise SemanticNodeError(
                f"Runtime handler {handler.handler_id} cannot compile a "
                "replacement effect"
            )
        return compiler(descriptor, context)


@lru_cache(maxsize=1)
def default_life_replacement_registry() -> LifeReplacementRegistry:
    registry = LifeReplacementRegistry((LifeGainMultiplierHandler(),))
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_life_change_replacement_effects(
    host: LifeReplacementHost,
    *,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
) -> tuple[ReplacementEffect, ...]:
    """Compile trusted life-change replacements from one source snapshot."""

    candidates = (
        list(sources)
        if sources is not None
        else host._semantic_event_sources(zones={"battlefield"})
    )
    registry = default_life_replacement_registry()
    effects: list[ReplacementEffect] = []
    for source in candidates:
        active_zone = (
            source_zones.get(source.object_id, source.zone)
            if source_zones is not None
            else source.zone
        )
        if (
            active_zone != "battlefield"
            or source.phased_out
            or source.controller not in host.active_seats
        ):
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(source),
            active_zone="battlefield",
            event="life.change",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                effects.append(
                    registry.replacement_effect(
                        descriptor,
                        LifeReplacementSourceContext(
                            source_ref=source.ref,
                            source_controller=source.controller,
                            component_id=f"{program.key}:{descriptor_index}",
                        ),
                    )
                )
    return tuple(effects)


__all__ = [
    "collect_life_change_replacement_effects",
    "default_life_replacement_registry",
    "LifeGainMultiplierHandler",
    "LifeGainMultiplierNode",
    "LifeReplacementHost",
    "LifeReplacementRegistry",
    "LifeReplacementSourceContext",
]
