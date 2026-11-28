from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..replacement_effects import (
    ReplacementClass,
    ReplacementEffect,
)
from ..rules.capabilities import load_default_capability_registry
from ..rules.damage_capability_shapes import (
    CONTROLLER_DAMAGE_LIFE_FLOOR_HANDLER, controller_damage_result_life_floor_descriptor,
)
from ..turn_match_outcome_model import ResolvedTurnMatchOutcomeProtection
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .current_ability_components import program_has_current_ability_fragments
from .life_replacements import collect_life_change_replacement_effects


_LIFE_FLOOR_HANDLER_ID = "replacement.damage.result.life_floor.v1"
_RELATIONS = {"any", "source_controller", "opponent"}


class DamageResultReplacementHost(Protocol):
    semantics: Any
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


@dataclass(frozen=True, slots=True)
class DamageResultReplacementSourceContext:
    source_ref: str
    source_controller: str
    component_id: str = ""

    def __post_init__(self) -> None:
        if not self.source_ref or not self.source_controller:
            raise SemanticNodeError(
                "Damage-result replacements require a source and controller"
            )


@dataclass(frozen=True, slots=True)
class DamageResultLifeFloorNode:
    affected_player_relation: str
    requires_controlled_creature: bool
    minimum_life: int


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
class DamageResultLifeFloorHandler:
    handler_id: str = _LIFE_FLOOR_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.damage.result.life_floor"
    event: str = "damage.results"
    rule_references: tuple[str, ...] = (
        "120.4c",
        "614.1",
        "616.1",
        "616.1f",
        "616.1g",
    )
    capability_dependencies: tuple[str, ...] = (
        "damage.result.replacement_order",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DamageResultLifeFloorNode:
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
                "Damage-result life-floor condition must be an object"
            )
        exact_fields(
            condition,
            {
                "affected_player_relation",
                "requires_controlled_creature",
            },
            field="damage-result life-floor condition",
        )
        requires_creature = condition["requires_controlled_creature"]
        if type(requires_creature) is not bool:
            raise SemanticNodeError(
                "requires_controlled_creature must be a boolean"
            )
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Damage-result life-floor modification must be an object"
            )
        exact_fields(
            modification,
            {"minimum_life"},
            field="damage-result life-floor modification",
        )
        minimum = modification["minimum_life"]
        if type(minimum) is not int:
            raise SemanticNodeError("minimum_life must be an integer")
        return DamageResultLifeFloorNode(
            affected_player_relation=_relation(
                condition["affected_player_relation"],
                field="condition.affected_player_relation",
            ),
            requires_controlled_creature=requires_creature,
            minimum_life=minimum,
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DamageResultReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        conditions: dict[str, Any] = {
            "subject_kind": {"eq": "player"},
            "life_loss_amount": {"not_in": [0]},
            "life_after_without_replacement": {"lt": node.minimum_life},
        }
        affected = _relation_condition(
            node.affected_player_relation,
            context.source_controller,
        )
        if affected is not None:
            conditions["affected_player"] = affected
        if node.requires_controlled_creature:
            conditions["controls_creature"] = {"eq": True}
        component_id = context.component_id or str(node.minimum_life)
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
                    "op": "cap_result_life_loss",
                    "minimum": node.minimum_life,
                },
            ),
            label=(
                f"{context.source_ref}: damage cannot reduce life below "
                f"{node.minimum_life}"
            ),
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DamageResultReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


@dataclass(frozen=True, slots=True)
class ControllerDamageResultLifeFloorHandler(DamageResultLifeFloorHandler):
    handler_id: str = CONTROLLER_DAMAGE_LIFE_FLOOR_HANDLER
    schema_version: int = 2

    def validate(self, descriptor: Mapping[str, Any]) -> DamageResultLifeFloorNode:
        if (type(descriptor.get("schema_version")) is not int
                or dict(descriptor) != controller_damage_result_life_floor_descriptor()):
            raise SemanticNodeError("Controller damage life floor requires its exact one-life result")
        return DamageResultLifeFloorNode("source_controller", False, 1)

    def replacement_effect(self, descriptor, context) -> ReplacementEffect:
        effect = DamageResultLifeFloorHandler.replacement_effect(self, descriptor, context)
        return replace(effect, conditions={**effect.conditions, "life_before": {"gte": 1}})


class DamageResultReplacementRegistry(
    RuntimeComponentRegistry[
        DamageResultReplacementSourceContext,
        ReplacementEffect,
    ]
):
    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DamageResultReplacementSourceContext,
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
def default_damage_result_replacement_registry(
) -> DamageResultReplacementRegistry:
    registry = DamageResultReplacementRegistry(
        (DamageResultLifeFloorHandler(), ControllerDamageResultLifeFloorHandler())
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_damage_result_replacement_effects(
    host: DamageResultReplacementHost,
    *,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
) -> tuple[ReplacementEffect, ...]:
    """Compile trusted result replacements from one pre-mutation snapshot."""

    candidates = (
        list(sources)
        if sources is not None
        else host._semantic_event_sources(zones={"battlefield"})
    )
    registry = default_damage_result_replacement_registry()
    effects: list[ReplacementEffect] = list(
        collect_life_change_replacement_effects(
            host,
            sources=candidates,
            source_zones=source_zones,
        )
    )
    journal = getattr(getattr(host, "state", None), "continuous_effects", None)
    for rule in journal or ():
        if isinstance(rule, ResolvedTurnMatchOutcomeProtection) and rule.controller in host.active_seats:
            effects.append(registry.replacement_effect(
                controller_damage_result_life_floor_descriptor(),
                DamageResultReplacementSourceContext(rule.source_id, rule.controller, rule.effect_id),
            ))
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
            event="damage.results",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                if descriptor.get("handler_id") == CONTROLLER_DAMAGE_LIFE_FLOOR_HANDLER and (
                    source.face_down or not program_has_current_ability_fragments(program, host._effective_card_data(source))
                ):
                    continue
                effects.append(
                    registry.replacement_effect(
                        descriptor,
                        DamageResultReplacementSourceContext(
                            source_ref=source.ref,
                            source_controller=source.controller,
                            component_id=f"{program.key}:{descriptor_index}",
                        ),
                    )
                )
    return tuple(effects)
