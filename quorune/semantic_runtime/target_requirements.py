"""Current static requirements for cast and activation target choices."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .component_registry import exact_fields
from .context import SemanticNodeError


FLAGBEARER_TARGET_EVENT = "target.announcement.requirement"
FLAGBEARER_TARGET_TEMPLATE = "opponent-flagbearer-target-requirement-v1"
FLAGBEARER_TARGET_CAPABILITY = "target.requirement.opponent_flagbearer"


@dataclass(frozen=True, slots=True)
class FlagbearerTargetRequirementHandler:
    handler_id: str = "ability.static.flagbearer-target-requirement.v1"
    schema_version: int = 1
    family: str = "ability.static.target_requirement"
    event: str = FLAGBEARER_TARGET_EVENT
    rule_references: tuple[str, ...] = ("115.1a", "115.1b", "115.1c", "601.2c", "602.2b", "611.3a", "611.3b")
    capability_dependencies: tuple[str, ...] = (FLAGBEARER_TARGET_CAPABILITY,)

    def descriptor(self) -> dict[str, Any]:
        return {"handler_id": self.handler_id, "schema_version": self.schema_version, "event": self.event}

    def validate(self, value: Mapping[str, Any]) -> None:
        exact_fields(value, {"handler_id", "schema_version", "event"}, field="Flagbearer target requirement")
        if type(value["schema_version"]) is not int or dict(value) != self.descriptor():
            raise SemanticNodeError("Flagbearer requirements need their exact static descriptor")

    def lower(self, value, context):
        self.validate(value)
        return ()


class TargetRequirementHost(Protocol):
    state: Any
    semantics: Any
    active_seats: list[str]

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...
    def _effective_static_component_keys(self, card: Any) -> tuple[str, ...]: ...
    def _effective_type_parts(self, card: Any) -> tuple[set[str], set[str], set[str]]: ...


def flagbearer_requirement_dependencies(program: Any) -> tuple[str, ...]:
    handler = FlagbearerTargetRequirementHandler()
    if not any(value.get("handler_id") == handler.handler_id for value in program.handlers):
        return ()
    if (
        (program.event, program.active_zone) != (handler.event, "battlefield")
        or program.provenance.get("template_id") != FLAGBEARER_TARGET_TEMPLATE
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or program.event_condition is not None or program.target_schema is not None
        or program.cost_schema is not None or program.effects or len(program.handlers) != 1
    ):
        raise ValueError("Flagbearer requirements need one current battlefield component")
    handler.validate(program.handlers[0])
    return handler.capability_dependencies


def current_flagbearer_target_refs(host: TargetRequirementHost, controller: str) -> frozenset[str]:
    """Read public Flagbearers only when an opposing current requirement exists."""
    programs = host.semantics.programs_for_event(FLAGBEARER_TARGET_EVENT)
    if not programs:
        return frozenset()
    active = frozenset(host.active_seats)
    keys = {
        program.key for program in programs
        if flagbearer_requirement_dependencies(program) and host.semantic_program_is_current_trusted(program)
    }
    if not keys:
        return frozenset()
    public_permanents = tuple(
        card for card in host.state.cards.values()
        if card.zone == "battlefield" and not card.phased_out and card.controller in active
    )
    if not any(card.controller != controller and keys.intersection(host._effective_static_component_keys(card))
               for card in public_permanents):
        return frozenset()
    return frozenset(card.ref for card in public_permanents if "flagbearer" in host._effective_type_parts(card)[1])
