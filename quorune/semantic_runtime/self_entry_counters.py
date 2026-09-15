from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..card_program_faces import program_matches_face
from ..entry_counter_model import (
    DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID,
    DynamicEntryCounterAmountSpec,
    EntryCounterError,
)
from ..entry_counters import dynamic_entry_counter_amount
from ..replacement_effects import (
    CreateAffectedObjectCounter,
    ReplacementClass,
    ReplacementEffect,
)
from .component_registry import exact_fields
from .context import SemanticNodeError
from .zone_replacement_model import (
    ZoneChangeReplacementContext,
    ZoneChangeSubjectSnapshot,
    ZoneDestinationIntent,
)


SELF_ENTRY_COUNTER_HANDLER_ID = "replacement.zone.self-entry-counter.v1"


@dataclass(frozen=True, slots=True)
class SelfEntryCounterNode:
    counter_name: str
    amount: int
    optional: bool
    rule_id: str

    def __post_init__(self) -> None:
        if type(self.counter_name) is not str:
            raise SemanticNodeError("Self-entry counter name must be a string")
        name = " ".join(self.counter_name.casefold().split())
        if not name:
            raise SemanticNodeError("Self-entry counter name must be nonempty")
        if type(self.amount) is not int or self.amount < 1:
            raise SemanticNodeError(
                "Self-entry counter amount must be a positive integer"
            )
        if type(self.optional) is not bool:
            raise SemanticNodeError("Self-entry counter optional must be boolean")
        if type(self.rule_id) is not str or not self.rule_id.strip():
            raise SemanticNodeError("Self-entry counter requires rule identity")
        object.__setattr__(self, "counter_name", name)


@dataclass(frozen=True, slots=True)
class SelfEntryCounterHandler:
    handler_id: str = SELF_ENTRY_COUNTER_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.self-entry-counter"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "122.6",
        "614.1c",
        "614.12",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.placement.quantity_replacement",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> SelfEntryCounterNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "counter_name",
                "amount",
                "optional",
                "rule_id",
            },
            field="self-entry counter handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Self-entry counter handler identity changed")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError("Unsupported self-entry counter schema version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError("Self-entry counter must handle zone changes")
        return SelfEntryCounterNode(
            counter_name=descriptor["counter_name"],
            amount=descriptor["amount"],
            optional=descriptor["optional"],
            rule_id=descriptor["rule_id"],
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        if subject.destination_controller is None:
            raise SemanticNodeError(
                "Battlefield self-entry counters require a destination controller"
            )
        if not component_id:
            raise SemanticNodeError(
                "Self-entry counters require stable component identity"
            )
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}",
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
            },
            operations=(
                CreateAffectedObjectCounter(
                    counter_name=node.counter_name,
                    amount=node.amount,
                    placing_player=subject.destination_controller,
                    source_ref=subject.object_ref,
                    sequence=0,
                ),
            ),
            optional=node.optional,
            label=(
                f"{subject.object_ref}: enter with {node.amount} "
                f"{node.counter_name} counter(s)"
            ),
        )


@dataclass(frozen=True, slots=True)
class DynamicSelfEntryCounterNode:
    counter_name: str
    amount_spec: DynamicEntryCounterAmountSpec
    rule_id: str

    def __post_init__(self) -> None:
        if type(self.counter_name) is not str:
            raise SemanticNodeError(
                "Dynamic self-entry counter name must be a string"
            )
        name = " ".join(self.counter_name.casefold().split())
        if not name:
            raise SemanticNodeError(
                "Dynamic self-entry counter name must be nonempty"
            )
        if not isinstance(self.amount_spec, DynamicEntryCounterAmountSpec):
            raise SemanticNodeError(
                "Dynamic self-entry counter amount must be typed"
            )
        if type(self.rule_id) is not str or not self.rule_id.strip():
            raise SemanticNodeError(
                "Dynamic self-entry counter requires rule identity"
            )
        object.__setattr__(self, "counter_name", name)


@dataclass(frozen=True, slots=True)
class DynamicSelfEntryCounterHandler:
    handler_id: str = DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.dynamic-self-entry-counter"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "107.3m",
        "122.6",
        "614.1c",
        "614.12",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.dynamic_self_entry",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> DynamicSelfEntryCounterNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "counter_name",
                "amount_spec",
                "rule_id",
            },
            field="dynamic self-entry counter handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Dynamic self-entry counter handler identity changed"
            )
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported dynamic self-entry counter schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Dynamic self-entry counter must handle zone changes"
            )
        try:
            amount_spec = DynamicEntryCounterAmountSpec.from_dict(
                descriptor["amount_spec"]
            )
        except (EntryCounterError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return DynamicSelfEntryCounterNode(
            counter_name=descriptor["counter_name"],
            amount_spec=amount_spec,
            rule_id=descriptor["rule_id"],
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> ReplacementEffect | None:
        node = self.validate(descriptor)
        if subject.destination_controller is None:
            raise SemanticNodeError(
                "Dynamic self-entry counters require a destination controller"
            )
        if not component_id:
            raise SemanticNodeError(
                "Dynamic self-entry counters require stable component identity"
            )
        amount = subject.self_entry_counter_amounts.get(component_id)
        if type(amount) is not int or amount < 0:
            raise SemanticNodeError(
                "Dynamic self-entry counter amount is missing from its snapshot"
            )
        if amount == 0:
            return None
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}",
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
            },
            operations=(
                CreateAffectedObjectCounter(
                    counter_name=node.counter_name,
                    amount=amount,
                    placing_player=subject.destination_controller,
                    source_ref=subject.object_ref,
                    sequence=0,
                ),
            ),
            optional=False,
            label=(
                f"{subject.object_ref}: enter with {amount} "
                f"{node.counter_name} counter(s)"
            ),
        )


def program_static_component_is_applicable(
    host: Any,
    program: Any,
    card: Any,
) -> bool:
    """Apply the shared layer-6 component-presence result when required."""

    return (
        CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or program.key in host._effective_static_component_keys(card)
    )


def dynamic_self_entry_counter_amounts_from_programs(
    host: Any,
    *,
    programs: Sequence[Any],
    record: Any,
    card: Any,
    destination: str,
    destination_controller: str | None,
    prospective_name: str,
    mana_colors_spent: Sequence[str],
) -> Mapping[str, int]:
    """Freeze component-keyed amounts from applicable typed programs."""

    if destination != "battlefield" or destination_controller is None:
        return {}
    handler = DynamicSelfEntryCounterHandler()
    amounts: dict[str, int] = {}
    for program in programs:
        if not program_matches_face(
            record,
            program,
            card,
            prospective_name=prospective_name or None,
        ) or not program_static_component_is_applicable(host, program, card):
            continue
        for descriptor_index, descriptor in enumerate(program.handlers):
            if (
                descriptor.get("handler_id")
                != DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID
            ):
                continue
            node = handler.validate(descriptor)
            component_id = f"{program.key}:{descriptor_index}"
            amounts[component_id] = dynamic_entry_counter_amount(
                host,
                card=card,
                destination_controller=destination_controller,
                amount_spec=node.amount_spec,
                mana_colors_spent=mana_colors_spent,
            )
    return dict(sorted(amounts.items()))


def _trusted_dynamic_entry_programs(host: Any, record: Any) -> tuple[Any, ...]:
    return tuple(
        program
        for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone="all",
            event="zone.change",
        )
        if host.semantic_program_is_current_trusted(program)
    )


def dynamic_self_entry_counter_amounts(
    host: Any,
    *,
    card: Any,
    destination: str,
    destination_controller: str | None,
    mana_colors_spent: Sequence[str],
) -> Mapping[str, int]:
    """Freeze one entering card's typed dynamic counter amounts."""

    record = host.card_record(card)
    if record is None:
        return {}
    return dynamic_self_entry_counter_amounts_from_programs(
        host,
        programs=_trusted_dynamic_entry_programs(host, record),
        record=record,
        card=card,
        destination=destination,
        destination_controller=destination_controller,
        prospective_name=str(card.active_face or ""),
        mana_colors_spent=mana_colors_spent,
    )


def subject_dynamic_self_entry_amounts(
    host: Any,
    *,
    record: Any | None,
    card: Any,
    destination: str,
    destination_controller: str | None,
    prospective_name: str,
    supplied_amounts: Mapping[str, Mapping[str, int]],
    mana_colors_spent: Mapping[str, Sequence[str]],
) -> Mapping[str, int]:
    if card.object_id in supplied_amounts:
        return supplied_amounts[card.object_id]
    if record is None:
        return {}
    return dynamic_self_entry_counter_amounts_from_programs(
        host,
        programs=_trusted_dynamic_entry_programs(host, record),
        record=record,
        card=card,
        destination=destination,
        destination_controller=destination_controller,
        prospective_name=prospective_name,
        mana_colors_spent=tuple(mana_colors_spent.get(card.object_id, ())),
    )


__all__ = [
    "DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID",
    "DynamicSelfEntryCounterHandler",
    "DynamicSelfEntryCounterNode",
    "dynamic_self_entry_counter_amounts",
    "dynamic_self_entry_counter_amounts_from_programs",
    "program_static_component_is_applicable",
    "SELF_ENTRY_COUNTER_HANDLER_ID",
    "SelfEntryCounterHandler",
    "SelfEntryCounterNode",
    "subject_dynamic_self_entry_amounts",
]
