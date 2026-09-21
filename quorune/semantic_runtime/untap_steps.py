from __future__ import annotations

"""CardProgram runtime components for CR 502 participation."""

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_query_result
from ..rules.capabilities import load_default_capability_registry
from ..untap_step import (
    plan_untap_step,
    UntapInstruction,
    UntapStepParticipation,
    UntapStepPlan,
    UntapSubjectRelation,
    UntapTurnRelation,
)
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .activation_restrictions import attached_activation_untap_participations


UNTAP_STEP_HANDLER_ID = "participation.untap-step.static.v1"
_SOURCE_STATES = {"any", "untapped"}
_CONTROLLER_RELATIONS = {"any", "source_controller"}


class UntapStepSemantics(Protocol):
    def runtime_handler_programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str,
        event: str,
    ) -> Sequence[Any]: ...


class UntapStepHost(Protocol):
    state: Any
    semantics: UntapStepSemantics
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


@dataclass(frozen=True, slots=True)
class UntapStepSourceContext:
    source_object_id: str
    source_ref: str
    source_controller: str
    source_tapped: bool
    component_id: str
    attached_object_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "source_object_id",
            "source_ref",
            "source_controller",
            "component_id",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"Untap-step {field_name} must be a nonempty string"
                )
        if type(self.source_tapped) is not bool:
            raise SemanticNodeError(
                "Untap-step source tapped state must be boolean"
            )
        if self.attached_object_id is not None and (
            type(self.attached_object_id) is not str
            or not self.attached_object_id
        ):
            raise SemanticNodeError(
                "Untap-step attached object identity must be nonempty"
            )


@dataclass(frozen=True, slots=True)
class UntapStepNode:
    source_state: str
    turn_relation: UntapTurnRelation
    subject_relation: UntapSubjectRelation
    controller_relation: str
    predicate: ObjectQuerySpec
    instruction: UntapInstruction
    maximum: int | None


@dataclass(frozen=True, slots=True)
class StaticUntapStepParticipationHandler:
    handler_id: str = UNTAP_STEP_HANDLER_ID
    schema_version: int = 1
    family: str = "participation.untap_step.static"
    event: str = "untap.step"
    rule_references: tuple[str, ...] = ("502.3",)
    capability_dependencies: tuple[str, ...] = (
        "untap.step.static_participation",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> UntapStepNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "subject",
                "instruction",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match the untap-step registry"
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
                "Untap-step condition must be an object"
            )
        exact_fields(
            condition,
            {"turn_relation", "source_state"},
            field="untap-step condition",
        )
        source_state = condition["source_state"]
        if type(source_state) is not str or source_state not in _SOURCE_STATES:
            raise SemanticNodeError(
                "Untap-step source state must be any or untapped"
            )
        try:
            turn_relation = UntapTurnRelation(condition["turn_relation"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(
                "Untap-step turn relation is unsupported"
            ) from exc

        subject = descriptor["subject"]
        if not isinstance(subject, Mapping):
            raise SemanticNodeError("Untap-step subject must be an object")
        exact_fields(
            subject,
            {"relation", "controller_relation", "predicate"},
            field="untap-step subject",
        )
        try:
            subject_relation = UntapSubjectRelation(subject["relation"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(
                "Untap-step subject relation is unsupported"
            ) from exc
        controller_relation = subject["controller_relation"]
        if (
            type(controller_relation) is not str
            or controller_relation not in _CONTROLLER_RELATIONS
        ):
            raise SemanticNodeError(
                "Untap-step controller relation must be any or source_controller"
            )
        if (
            subject_relation is not UntapSubjectRelation.QUERY
            and controller_relation != "any"
        ):
            raise SemanticNodeError(
                "Pinned untap-step subjects reserve controller relations"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(subject["predicate"])
        except ObjectQueryError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (
            predicate.owner is not None
            or predicate.controller is not None
            or predicate.exclude_ref is not None
            or predicate.known_to_actor is not None
        ):
            raise SemanticNodeError(
                "Untap-step predicates reserve owner, controller, visibility, and exclusions"
            )
        if predicate.zones not in {(), ("battlefield",)}:
            raise SemanticNodeError(
                "Untap-step predicates apply only on the battlefield"
            )
        if predicate.include_phased_out:
            raise SemanticNodeError(
                "Untap-step participation excludes phased-out objects"
            )

        instruction = descriptor["instruction"]
        if not isinstance(instruction, Mapping):
            raise SemanticNodeError(
                "Untap-step instruction must be an object"
            )
        exact_fields(
            instruction,
            {"kind", "maximum"},
            field="untap-step instruction",
        )
        try:
            kind = UntapInstruction(instruction["kind"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(
                "Untap-step instruction kind is unsupported"
            ) from exc
        maximum = instruction["maximum"]
        if kind is UntapInstruction.LIMIT:
            if type(maximum) is not int or maximum < 0:
                raise SemanticNodeError(
                    "Untap-step limits require a nonnegative integer maximum"
                )
            if subject_relation is not UntapSubjectRelation.QUERY:
                raise SemanticNodeError(
                    "Untap-step limits require a query subject"
                )
        elif maximum is not None:
            raise SemanticNodeError(
                "Only untap-step limits may declare a maximum"
            )
        if (
            kind is UntapInstruction.ADDITIONAL
            and turn_relation is not UntapTurnRelation.OTHER_PLAYER
        ):
            raise SemanticNodeError(
                "Additional untaps require another player's untap step"
            )
        return UntapStepNode(
            source_state=source_state,
            turn_relation=turn_relation,
            subject_relation=subject_relation,
            controller_relation=controller_relation,
            predicate=predicate,
            instruction=kind,
            maximum=maximum,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: UntapStepSourceContext,
    ) -> tuple[UntapStepParticipation, ...]:
        node = self.validate(descriptor)
        if node.source_state == "untapped" and context.source_tapped:
            return ()
        subject_object_id = None
        if node.subject_relation is UntapSubjectRelation.SOURCE:
            subject_object_id = context.source_object_id
        elif node.subject_relation is UntapSubjectRelation.ATTACHED_OBJECT:
            subject_object_id = context.attached_object_id
            if subject_object_id is None:
                return ()
        predicate = replace(
            node.predicate,
            zones=("battlefield",),
            controller=(
                context.source_controller
                if node.controller_relation == "source_controller"
                else None
            ),
        )
        return (
            UntapStepParticipation(
                participation_id=(
                    f"{self.handler_id}:{context.source_object_id}:"
                    f"{context.component_id}"
                ),
                source_object_id=context.source_object_id,
                source_ref=context.source_ref,
                source_controller=context.source_controller,
                instruction=node.instruction,
                subject_relation=node.subject_relation,
                turn_relation=node.turn_relation,
                predicate=predicate,
                subject_object_id=subject_object_id,
                maximum=node.maximum,
            ),
        )


class UntapStepComponentRegistry(
    RuntimeComponentRegistry[
        UntapStepSourceContext,
        UntapStepParticipation,
    ]
):
    pass


@lru_cache(maxsize=1)
def default_untap_step_component_registry() -> UntapStepComponentRegistry:
    registry = UntapStepComponentRegistry(
        (StaticUntapStepParticipationHandler(),)
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_untap_step_participations(
    host: UntapStepHost,
) -> tuple[UntapStepParticipation, ...]:
    registry = default_untap_step_component_registry()
    participations: list[UntapStepParticipation] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if (
            source.zone != "battlefield"
            or source.phased_out
            or source.controller not in host.active_seats
        ):
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event="untap.step",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                participations.extend(
                    registry.lower(
                        descriptor,
                        UntapStepSourceContext(
                            source_object_id=source.object_id,
                            source_ref=source.ref,
                            source_controller=source.controller,
                            source_tapped=bool(source.tapped),
                            component_id=f"{program.key}:{descriptor_index}",
                            attached_object_id=(
                                source.attached_to
                                if source.attached_to in host.state.cards
                                else None
                            ),
                        ),
                    )
                )
    participations.extend(attached_activation_untap_participations(host))
    return tuple(
        sorted(
            participations,
            key=lambda value: value.participation_id,
        )
    )


def current_untap_step_plan(
    host: UntapStepHost,
    active_player: str,
) -> UntapStepPlan:
    rows = []
    for card in host.state.cards.values():
        if card.zone != "battlefield" or card.phased_out:
            continue
        effective = host._effective_card_data(card)
        rows.append(
            object_query_result(
                card,
                effective,
                type_parts=host._type_parts(
                    str(effective.get("type_line") or "")
                ),
                known_to_actor=True,
                attached_to_ref=(
                    host.state.cards[card.attached_to].ref
                    if card.attached_to in host.state.cards
                    else None
                ),
            )
        )
    return plan_untap_step(
        active_player,
        rows,
        collect_untap_step_participations(host),
    )


__all__ = [
    "collect_untap_step_participations",
    "current_untap_step_plan",
    "default_untap_step_component_registry",
    "StaticUntapStepParticipationHandler",
    "UNTAP_STEP_HANDLER_ID",
    "UntapStepComponentRegistry",
    "UntapStepHost",
    "UntapStepNode",
    "UntapStepSourceContext",
]
