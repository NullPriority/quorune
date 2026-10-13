from __future__ import annotations
from ..entry_copy import copiable_oracle_id

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Mapping, Protocol, Sequence

from ..replacement_effects import (
    AffectedObject,
    ReplaceableEvent,
    ReplacementBatchChoice,
    ReplacementClass,
    ReplacementEffect,
    ReplacementEventBatch,
    ReplacementSelection,
    advance_replacement_batch,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import (
    RuntimeComponentRegistry,
    exact_fields,
    nonempty_strings,
)
from .context import SemanticNodeError


_QUANTITY_HANDLER_ID = "replacement.counter.quantity.v1"
_QUANTITY_V2_HANDLER_ID = "replacement.counter.quantity.v2"
_RELATIONS = {"any", "source_controller", "opponent"}
_EFFECT_SCOPES = {"any", "effect_only"}
_TARGET_KINDS = {"permanent", "player"}
_COUNTER_TARGET_GOBLIN = "goblin"
_COUNTER_TARGET_VEHICLE = "vehicle"
_TARGET_TYPE_PREDICATES = {
    "army",
    "artifact",
    "creature",
    _COUNTER_TARGET_GOBLIN,
    "orc",
    "planet",
    "spacecraft",
    _COUNTER_TARGET_VEHICLE,
}


class CounterReplacementHost(Protocol):
    semantics: Any
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


@dataclass(frozen=True, slots=True)
class CounterQuantityReplacementNode:
    placing_player_relation: str
    target_controller_relation: str
    counter_names: tuple[str, ...]
    target_types_all: tuple[str, ...]
    multiplier: int
    additional: int


@dataclass(frozen=True, slots=True)
class CounterQuantityReplacementV2Node:
    effect_scope: str
    placing_player_relation: str
    target_controller_relation: str
    target_kinds: tuple[str, ...]
    counter_names: tuple[str, ...]
    target_types_all: tuple[str, ...]
    target_types_any: tuple[str, ...]
    multiplier: int
    additional: int


@dataclass(frozen=True, slots=True)
class CounterReplacementSourceContext:
    source_ref: str
    source_controller: str
    component_id: str = ""

    def __post_init__(self) -> None:
        if not self.source_ref or not self.source_controller:
            raise SemanticNodeError(
                "Counter replacement sources require a ref and controller"
            )


@dataclass(frozen=True, slots=True)
class CounterPlacementEventSpec:
    event_id: str
    subject_kind: Literal["player", "permanent"]
    subject_id: str
    placing_player: str
    counter_name: str
    amount: int
    source_ref: str | None
    effect_generated: bool
    owner: str | None = None
    controller: str | None = None
    target_zone: str | None = None
    target_types: tuple[str, ...] = ()
    logical_object_id: str | None = None
    prospective_subject: bool = False

    def __post_init__(self) -> None:
        if (
            not self.event_id
            or self.subject_kind not in {"player", "permanent"}
            or not self.subject_id
        ):
            raise SemanticNodeError(
                "Counter placement events require stable subject identity"
            )
        if not self.placing_player:
            raise SemanticNodeError(
                "Counter placement events require the placing player"
            )
        if (
            not self.counter_name
            or type(self.amount) is not int
            or self.amount < 1
        ):
            raise SemanticNodeError(
                "Counter placement events require a positive named amount"
            )
        if self.subject_kind == "player":
            if any(
                value is not None
                for value in (
                    self.owner,
                    self.controller,
                    self.target_zone,
                    self.logical_object_id,
                )
            ) or self.target_types:
                raise SemanticNodeError(
                    "Player counter events cannot carry permanent identity"
                )
        elif not self.owner or not self.target_zone:
            raise SemanticNodeError(
                "Permanent counter events require owner and zone identity"
            )
        if type(self.prospective_subject) is not bool:
            raise SemanticNodeError(
                "Counter placement prospective-subject state must be boolean"
            )
        if self.prospective_subject and (
            self.subject_kind != "permanent"
            or self.target_zone != "battlefield"
            or not self.logical_object_id
        ):
            raise SemanticNodeError(
                "Prospective counter subjects require entering permanent "
                "identity"
            )

    @property
    def target_kind(self) -> str:
        if self.subject_kind == "player":
            return "player"
        return "permanent" if self.target_zone == "battlefield" else "card"

    def event(self) -> ReplaceableEvent:
        return ReplaceableEvent(
            event_id=self.event_id,
            kind="counter.place",
            affected_player=(
                self.subject_id if self.subject_kind == "player" else None
            ),
            affected_object=(
                AffectedObject(
                    object_id=self.subject_id,
                    owner=str(self.owner),
                    controller=self.controller,
                )
                if self.subject_kind == "permanent"
                else None
            ),
            payload={
                "placing_player": self.placing_player,
                "target_controller": (
                    self.controller
                    if self.subject_kind == "permanent"
                    else self.subject_id
                ),
                "target_zone": self.target_zone,
                "target_logical_object_id": self.logical_object_id,
                "target_kind": self.target_kind,
                "target_types": sorted(set(self.target_types)),
                "counter_name": self.counter_name,
                "amount": self.amount,
                "requested_amount": self.amount,
                "source": self.source_ref,
                "effect_generated": self.effect_generated,
                **(
                    {"prospective_subject": True}
                    if self.prospective_subject
                    else {}
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class CounterPlacementReplacementResolution:
    batch: ReplacementEventBatch
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    pending: ReplacementBatchChoice | None


@dataclass(frozen=True, slots=True)
class CounterQuantityReplacementHandler:
    handler_id: str = _QUANTITY_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.counter.quantity"
    event: str = "counter.place"
    rule_references: tuple[str, ...] = (
        "122.1",
        "122.6",
        "614.1",
        "614.16",
        "616.1",
        "616.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.placement.quantity_replacement",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CounterQuantityReplacementNode:
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
                "Counter replacement condition must be an object"
            )
        exact_fields(
            condition,
            {
                "placing_player_relation",
                "target_controller_relation",
                "counter_names",
                "target_types_all",
                "effect_generated",
            },
            field="counter replacement condition",
        )
        placing_relation = str(condition["placing_player_relation"])
        target_relation = str(condition["target_controller_relation"])
        if placing_relation not in _RELATIONS or target_relation not in _RELATIONS:
            raise SemanticNodeError(
                "Counter replacement relations must be any, "
                "source_controller, or opponent"
            )
        if condition["effect_generated"] is not True:
            raise SemanticNodeError(
                "Counter quantity replacements currently require "
                "effect_generated=true"
            )
        counter_names = tuple(
            " ".join(value.casefold().split())
            for value in nonempty_strings(
                condition["counter_names"],
                field="condition.counter_names",
            )
        )
        target_types = tuple(
            value.casefold()
            for value in nonempty_strings(
                condition["target_types_all"],
                field="condition.target_types_all",
            )
        )
        if len(counter_names) != len(set(counter_names)):
            raise SemanticNodeError(
                "condition.counter_names must remain unique after normalization"
            )
        if len(target_types) != len(set(target_types)):
            raise SemanticNodeError(
                "condition.target_types_all must remain unique after normalization"
            )
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Counter replacement modification must be an object"
            )
        exact_fields(
            modification,
            {"multiplier", "additional"},
            field="counter replacement modification",
        )
        multiplier = modification["multiplier"]
        additional = modification["additional"]
        if type(multiplier) is not int or multiplier < 1:
            raise SemanticNodeError(
                "Counter replacement multiplier must be a positive integer"
            )
        if type(additional) is not int or additional < 0:
            raise SemanticNodeError(
                "Counter replacement additional amount must be nonnegative"
            )
        if multiplier == 1 and additional == 0:
            raise SemanticNodeError(
                "Counter replacement must change the placed amount"
            )
        return CounterQuantityReplacementNode(
            placing_player_relation=placing_relation,
            target_controller_relation=target_relation,
            counter_names=counter_names,
            target_types_all=target_types,
            multiplier=multiplier,
            additional=additional,
        )

    @staticmethod
    def _relation_condition(
        relation: str,
        source_controller: str,
    ) -> Mapping[str, Any] | None:
        if relation == "any":
            return None
        if relation == "source_controller":
            return {"eq": source_controller}
        return {"not_in": [source_controller, None]}

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: CounterReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        conditions: dict[str, Any] = {
            "effect_generated": {"eq": True},
            "target_kind": {"eq": "permanent"},
        }
        placing = self._relation_condition(
            node.placing_player_relation,
            context.source_controller,
        )
        if placing is not None:
            conditions["placing_player"] = placing
        target = self._relation_condition(
            node.target_controller_relation,
            context.source_controller,
        )
        if target is not None:
            conditions["target_controller"] = target
        if node.counter_names:
            conditions["counter_name"] = {"in": list(node.counter_names)}
        if node.target_types_all:
            conditions["target_types"] = {
                "contains_all": list(node.target_types_all)
            }
        operations: list[Mapping[str, Any]] = []
        if node.multiplier != 1:
            operations.append(
                {
                    "op": "multiply",
                    "field": "amount",
                    "factor": node.multiplier,
                }
            )
        if node.additional:
            operations.append(
                {
                    "op": "add",
                    "field": "amount",
                    "amount": node.additional,
                }
            )
        component_id = context.component_id or (
            f"{node.multiplier}x+{node.additional}"
        )
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:{component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions=conditions,
            operations=tuple(operations),
            label=(
                f"{context.source_ref}: change the number of counters placed"
            ),
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: CounterReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


def _validated_v2_condition(
    condition: Any,
) -> tuple[
    str,
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if not isinstance(condition, Mapping):
        raise SemanticNodeError("Counter replacement condition must be an object")
    exact_fields(
        condition,
        {
            "effect_scope",
            "placing_player_relation",
            "target_controller_relation",
            "target_kinds",
            "counter_names",
            "target_types_all",
            "target_types_any",
        },
        field="counter replacement condition",
    )
    effect_scope = str(condition["effect_scope"])
    if effect_scope not in _EFFECT_SCOPES:
        raise SemanticNodeError(
            "Counter replacement effect_scope must be any or effect_only"
        )
    placing_relation = str(condition["placing_player_relation"])
    target_relation = str(condition["target_controller_relation"])
    if placing_relation not in _RELATIONS or target_relation not in _RELATIONS:
        raise SemanticNodeError(
            "Counter replacement relations must be any, source_controller, "
            "or opponent"
        )
    raw_target_kinds = nonempty_strings(
        condition["target_kinds"], field="condition.target_kinds"
    )
    target_kinds = tuple(value.casefold() for value in raw_target_kinds)
    if (
        raw_target_kinds != target_kinds
        or tuple(sorted(set(target_kinds))) != target_kinds
        or not set(target_kinds).issubset(_TARGET_KINDS)
    ):
        raise SemanticNodeError(
            "condition.target_kinds must contain canonical sorted unique "
            "permanent/player values"
        )
    raw_counter_names = nonempty_strings(
        condition["counter_names"], field="condition.counter_names"
    )
    counter_names = tuple(
        " ".join(value.casefold().split()) for value in raw_counter_names
    )
    raw_target_types_all = nonempty_strings(
        condition["target_types_all"], field="condition.target_types_all"
    )
    target_types_all = tuple(value.casefold() for value in raw_target_types_all)
    raw_target_types_any = nonempty_strings(
        condition["target_types_any"], field="condition.target_types_any"
    )
    target_types_any = tuple(value.casefold() for value in raw_target_types_any)
    for field_name, raw_values, values in (
        ("counter_names", raw_counter_names, counter_names),
        ("target_types_all", raw_target_types_all, target_types_all),
        ("target_types_any", raw_target_types_any, target_types_any),
    ):
        if raw_values != values or tuple(sorted(set(values))) != values:
            raise SemanticNodeError(
                f"condition.{field_name} must contain canonical sorted "
                "unique values"
            )
    all_target_types = set((*target_types_all, *target_types_any))
    if not all_target_types.issubset(_TARGET_TYPE_PREDICATES):
        raise SemanticNodeError(
            "Counter replacement target type predicate is unsupported"
        )
    if target_types_all and target_types_any:
        raise SemanticNodeError(
            "Counter replacement cannot combine all and any type predicates"
        )
    if "player" in target_kinds and all_target_types:
        raise SemanticNodeError(
            "Typed counter targets cannot combine player scope with type predicates"
        )
    return (
        effect_scope,
        placing_relation,
        target_relation,
        target_kinds,
        counter_names,
        target_types_all,
        target_types_any,
    )


def _validated_v2_modification(modification: Any) -> tuple[int, int]:
    if not isinstance(modification, Mapping):
        raise SemanticNodeError("Counter replacement modification must be an object")
    exact_fields(
        modification,
        {"multiplier", "additional"},
        field="counter replacement modification",
    )
    multiplier = modification["multiplier"]
    additional = modification["additional"]
    if type(multiplier) is not int or multiplier < 1:
        raise SemanticNodeError(
            "Counter replacement multiplier must be a positive integer"
        )
    if type(additional) is not int or additional < 0:
        raise SemanticNodeError(
            "Counter replacement additional amount must be nonnegative"
        )
    if multiplier == 1 and additional == 0:
        raise SemanticNodeError("Counter replacement must change the placed amount")
    return multiplier, additional


@dataclass(frozen=True, slots=True)
class CounterQuantityReplacementV2Handler:
    """Closed counter-quantity vocabulary for effects, costs, and players."""

    handler_id: str = _QUANTITY_V2_HANDLER_ID
    schema_version: int = 2
    family: str = "replacement.counter.quantity"
    event: str = "counter.place"
    rule_references: tuple[str, ...] = (
        "122.1",
        "122.6",
        "614.1",
        "614.16",
        "616.1",
        "616.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.placement.quantity_replacement",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CounterQuantityReplacementV2Node:
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
        (
            effect_scope,
            placing_relation,
            target_relation,
            target_kinds,
            counter_names,
            target_types_all,
            target_types_any,
        ) = _validated_v2_condition(descriptor["condition"])
        multiplier, additional = _validated_v2_modification(
            descriptor["modification"]
        )
        return CounterQuantityReplacementV2Node(
            effect_scope=effect_scope,
            placing_player_relation=placing_relation,
            target_controller_relation=target_relation,
            target_kinds=target_kinds,
            counter_names=counter_names,
            target_types_all=target_types_all,
            target_types_any=target_types_any,
            multiplier=multiplier,
            additional=additional,
        )

    @staticmethod
    def _relation_condition(
        relation: str, source_controller: str
    ) -> Mapping[str, Any] | None:
        return CounterQuantityReplacementHandler._relation_condition(
            relation, source_controller
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: CounterReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        conditions: dict[str, Any] = {
            "target_kind": {"in": list(node.target_kinds)},
        }
        if node.effect_scope == "effect_only":
            conditions["effect_generated"] = {"eq": True}
        placing = self._relation_condition(
            node.placing_player_relation, context.source_controller
        )
        if placing is not None:
            conditions["placing_player"] = placing
        target = self._relation_condition(
            node.target_controller_relation, context.source_controller
        )
        if target is not None:
            conditions["target_controller"] = target
        if node.counter_names:
            conditions["counter_name"] = {"in": list(node.counter_names)}
        if node.target_types_all:
            conditions["target_types"] = {
                "contains_all": list(node.target_types_all)
            }
        if node.target_types_any:
            conditions["target_types"] = {
                "contains_any": list(node.target_types_any)
            }
        operations: list[Mapping[str, Any]] = []
        if node.multiplier != 1:
            operations.append(
                {
                    "op": "multiply",
                    "field": "amount",
                    "factor": node.multiplier,
                }
            )
        if node.additional:
            operations.append(
                {
                    "op": "add",
                    "field": "amount",
                    "amount": node.additional,
                }
            )
        component_id = context.component_id or (
            f"{node.multiplier}x+{node.additional}"
        )
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:{component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions=conditions,
            operations=tuple(operations),
            label=f"{context.source_ref}: change the number of counters placed",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: CounterReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


class CounterPlacementReplacementRegistry(
    RuntimeComponentRegistry[
        CounterReplacementSourceContext,
        ReplacementEffect,
    ]
):
    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: CounterReplacementSourceContext,
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
def default_counter_placement_replacement_registry(
) -> CounterPlacementReplacementRegistry:
    registry = CounterPlacementReplacementRegistry(
        (
            CounterQuantityReplacementHandler(),
            CounterQuantityReplacementV2Handler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_counter_placement_replacement_effects(
    host: CounterReplacementHost,
    *,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
) -> tuple[ReplacementEffect, ...]:
    """Compile active trusted counter replacements once per source batch."""

    candidates = (
        list(sources)
        if sources is not None
        else host._semantic_event_sources(zones={"battlefield"})
    )
    registry = default_counter_placement_replacement_registry()
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
            event="counter.place",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                effects.append(
                    registry.replacement_effect(
                        descriptor,
                        CounterReplacementSourceContext(
                            source_ref=source.ref,
                            source_controller=source.controller,
                            component_id=f"{program.key}:{descriptor_index}",
                        ),
                    )
                )
    return tuple(effects)


def resolve_counter_placement_replacements(
    *,
    batch_id: str,
    events: Sequence[ReplaceableEvent],
    effects: Sequence[ReplacementEffect],
    apnap_order: Sequence[str],
    selections: Sequence[str | None | Mapping[str, Any]] = (),
) -> CounterPlacementReplacementResolution:
    progress = advance_replacement_batch(
        ReplacementEventBatch(
            batch_id=batch_id,
            events=tuple(events),
            apnap_order=tuple(apnap_order),
        ),
        tuple(effects),
        selections=tuple(selections),
    )
    return CounterPlacementReplacementResolution(
        batch=progress.batch,
        effects=tuple(effects),
        journal=progress.batch.journal,
        pending=progress.pending,
    )
