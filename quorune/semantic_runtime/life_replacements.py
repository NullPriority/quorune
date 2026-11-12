from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..replacement_effects import ReplacementClass, ReplacementEffect
from ..rules.capabilities import load_default_capability_registry
from ..life_gain_permission import FixedLifeGainProhibitionSpec
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


_LIFE_GAIN_HANDLER_ID = "replacement.life.gain.multiplier.v1"
_LIFE_GAIN_ADDITION_HANDLER_ID = "replacement.life.gain.addition.v1"
_LIFE_GAIN_ADDITION_CAPABILITY = "life.gain.replacement.static_addition"
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


@dataclass(frozen=True, slots=True)
class LifeGainAdditionNode:
    affected_player_relation: str
    addition: int


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


def _life_gain_modification(descriptor, *, handler_id, schema_version, event):
    exact_fields(
        descriptor,
        {"handler_id", "schema_version", "event", "condition", "modification"},
        field="runtime handler",
    )
    if descriptor["handler_id"] != handler_id:
        raise SemanticNodeError("Runtime handler ID does not match registry")
    if descriptor["schema_version"] != schema_version:
        raise SemanticNodeError(f"Unsupported {handler_id} schema version")
    if descriptor["event"] != event:
        raise SemanticNodeError(f"{handler_id} must handle {event}")
    condition = descriptor["condition"]
    if not isinstance(condition, Mapping):
        raise SemanticNodeError("Life-gain replacement condition must be an object")
    exact_fields(condition, {"affected_player_relation"}, field="life-gain replacement condition")
    modification = descriptor["modification"]
    if not isinstance(modification, Mapping):
        raise SemanticNodeError("Life-gain replacement modification must be an object")
    relation = _relation(condition["affected_player_relation"], field="condition.affected_player_relation")
    return relation, modification


def _positive_life_gain_replacement(context, *, handler_id, event, relation, quantity, operation, label):
    conditions = {"direction": {"eq": "gain"}, "amount": {"not_in": [0]}}
    affected = _relation_condition(relation, context.source_controller)
    if affected is not None:
        conditions["affected_player"] = affected
    component_id = context.component_id or str(quantity)
    return ReplacementEffect(
        effect_id=f"{handler_id}:{context.source_ref}:{component_id}",
        source_id=context.source_ref,
        event_kind=event,
        replacement_class=ReplacementClass.OTHER,
        conditions=conditions,
        operations=(operation,),
        label=f"{context.source_ref}: {label}",
    )


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
        relation, modification = _life_gain_modification(
            descriptor, handler_id=self.handler_id, schema_version=self.schema_version, event=self.event,
        )
        exact_fields(modification, {"multiplier"}, field="life-gain replacement modification")
        multiplier = modification["multiplier"]
        if type(multiplier) is not int or multiplier < 2:
            raise SemanticNodeError("Life-gain multiplier must be an integer of at least 2")
        return LifeGainMultiplierNode(affected_player_relation=relation, multiplier=multiplier)

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: LifeReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        return _positive_life_gain_replacement(
            context, handler_id=self.handler_id, event=self.event, relation=node.affected_player_relation,
            quantity=node.multiplier, operation={"op": "multiply", "field": "amount", "factor": node.multiplier},
            label="multiply life gained",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: LifeReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


@dataclass(frozen=True, slots=True)
class LifeGainAdditionHandler:
    handler_id: str = _LIFE_GAIN_ADDITION_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.life.gain.addition"
    event: str = "life.change"
    rule_references: tuple[str, ...] = (
        "119.10", "120.4c", "614.1", "614.5", "616.1", "616.1f",
    )
    capability_dependencies: tuple[str, ...] = (_LIFE_GAIN_ADDITION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> LifeGainAdditionNode:
        relation, modification = _life_gain_modification(
            descriptor, handler_id=self.handler_id, schema_version=self.schema_version, event=self.event,
        )
        if type(descriptor["schema_version"]) is not int or relation != "source_controller":
            raise SemanticNodeError("Life-gain addition requires its versioned source-controller scope")
        exact_fields(modification, {"addition"}, field="life-gain replacement modification")
        amount = modification["addition"]
        if type(amount) is not int or amount < 1:
            raise SemanticNodeError("Life-gain addition must be a positive integer")
        return LifeGainAdditionNode(affected_player_relation=relation, addition=amount)

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: LifeReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        return _positive_life_gain_replacement(
            context, handler_id=self.handler_id, event=self.event, relation=node.affected_player_relation,
            quantity=node.addition, operation={"op": "add", "field": "amount", "amount": node.addition},
            label="add to life gained",
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
        ReplacementEffect | FixedLifeGainProhibitionSpec,
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
    from .life_prohibitions import LifeGainProhibitionHandler
    registry = LifeReplacementRegistry((LifeGainMultiplierHandler(), LifeGainAdditionHandler(), LifeGainProhibitionHandler()))
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
