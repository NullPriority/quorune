from __future__ import annotations

"""Typed static restrictions on activating abilities."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..ability_fragments import activation_prohibition_specs
from ..abilities import ActivatedAbility
from ..card_program_faces import program_matches_face
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


ACTIVATION_PERMISSION_EVENT = "activation.permission"
CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID = (
    "restriction.activation.chosen-name-nonmana.v1"
)
ATTACHED_ACTIVATION_PROHIBITION_FRAGMENT_ID = (
    "ability.fragment.activation-prohibition.v1"
)


def _normalized_name(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise SemanticNodeError(f"{field} must be a string")
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class ChosenNameNonmanaRestrictionNode:
    source_name_relation: str
    ability_scope: str


@dataclass(frozen=True, slots=True)
class ActivationRestrictionContext:
    restriction_source_ref: str
    chosen_name: str
    candidate_source_name: str
    candidate_is_mana_ability: bool

    def __post_init__(self) -> None:
        if (
            type(self.restriction_source_ref) is not str
            or not self.restriction_source_ref
        ):
            raise SemanticNodeError(
                "Activation restriction requires source identity"
            )
        object.__setattr__(
            self,
            "chosen_name",
            _normalized_name(self.chosen_name, field="Chosen name"),
        )
        object.__setattr__(
            self,
            "candidate_source_name",
            _normalized_name(
                self.candidate_source_name,
                field="Candidate source name",
            ),
        )
        if type(self.candidate_is_mana_ability) is not bool:
            raise SemanticNodeError(
                "Activation restriction mana-ability status must be boolean"
            )


@dataclass(frozen=True, slots=True)
class ActivationProhibition:
    restriction_source_ref: str
    candidate_source_name: str
    handler_id: str
    reason: str = "named_ability_prohibition"


@dataclass(frozen=True, slots=True)
class ChosenNameNonmanaProhibitionHandler:
    handler_id: str = CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID
    schema_version: int = 1
    family: str = "restriction.activation.chosen_name_nonmana"
    event: str = ACTIVATION_PERMISSION_EVENT
    rule_references: tuple[str, ...] = ("602.1", "602.5", "605.1a")
    capability_dependencies: tuple[str, ...] = (
        "activation.restriction.chosen_name_nonmana",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> ChosenNameNonmanaRestrictionNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "source_name_relation",
                "ability_scope",
            },
            field="chosen-name activation restriction",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Chosen-name activation-restriction handler identity changed"
            )
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported chosen-name activation-restriction schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Chosen-name activation restriction must handle {self.event}"
            )
        if descriptor["source_name_relation"] != "chosen_name":
            raise SemanticNodeError(
                "Chosen-name activation restriction requires chosen-name rules data"
            )
        if descriptor["ability_scope"] != "nonmana":
            raise SemanticNodeError(
                "Chosen-name activation restriction must exempt mana abilities"
            )
        return ChosenNameNonmanaRestrictionNode(
            source_name_relation="chosen_name",
            ability_scope="nonmana",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ActivationRestrictionContext,
    ) -> tuple[ActivationProhibition, ...]:
        self.validate(descriptor)
        if (
            context.candidate_is_mana_ability
            or not context.chosen_name
            or context.chosen_name != context.candidate_source_name
        ):
            return ()
        return (
            ActivationProhibition(
                restriction_source_ref=context.restriction_source_ref,
                candidate_source_name=context.candidate_source_name,
                handler_id=self.handler_id,
            ),
        )


class ActivationRestrictionRegistry(
    RuntimeComponentRegistry[
        ActivationRestrictionContext,
        ActivationProhibition,
    ]
):
    pass


@lru_cache(maxsize=1)
def default_activation_restriction_registry() -> ActivationRestrictionRegistry:
    registry = ActivationRestrictionRegistry(
        (ChosenNameNonmanaProhibitionHandler(),)
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


class ActivationRestrictionHost(Protocol):
    active_seats: Sequence[str]
    semantics: Any

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> Sequence[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _effective_ability_fragments(
        self,
        card: Any,
        *,
        error_type: type[Exception],
    ) -> Sequence[Any]: ...

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def current_activation_prohibitions(
    host: ActivationRestrictionHost,
    source: Any,
    ability: ActivatedAbility,
) -> tuple[ActivationProhibition, ...]:
    """Collect trusted current-face prohibitions for one candidate ability."""

    registry = default_activation_restriction_registry()
    candidate_name = (
        host._effective_card_data(source).get("name")
        or source.printed_name
    )
    prohibitions: list[ActivationProhibition] = []
    for specification in activation_prohibition_specs(
        host._effective_ability_fragments(source, error_type=RuntimeError)
    ):
        if specification.scope == "all" or not ability.mana_ability:
            prohibitions.append(
                ActivationProhibition(
                    restriction_source_ref=source.ref,
                    candidate_source_name=str(candidate_name),
                    handler_id=ATTACHED_ACTIVATION_PROHIBITION_FRAGMENT_ID,
                    reason="current_ability_fragment_prohibition",
                )
            )
    for restriction_source in host._semantic_event_sources(
        zones={"battlefield"}
    ):
        if (
            restriction_source.zone != "battlefield"
            or restriction_source.phased_out
            or restriction_source.controller not in host.active_seats
        ):
            continue
        record = host.card_record(restriction_source)
        if record is None:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            restriction_source.oracle_id,
            active_zone="battlefield",
            event=ACTIVATION_PERMISSION_EVENT,
        )
        for program in programs:
            if (
                not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(
                    record,
                    program,
                    restriction_source,
                )
            ):
                continue
            context = ActivationRestrictionContext(
                restriction_source_ref=restriction_source.ref,
                chosen_name=(
                    restriction_source.annotations.get("chosen_name") or ""
                ),
                candidate_source_name=candidate_name,
                candidate_is_mana_ability=ability.mana_ability,
            )
            for descriptor in program.handlers:
                if registry.describe(
                    str(descriptor.get("handler_id") or "")
                ) is not None:
                    prohibitions.extend(registry.lower(descriptor, context))
    return tuple(
        sorted(
            prohibitions,
            key=lambda value: (
                value.restriction_source_ref,
                value.handler_id,
                value.candidate_source_name,
            ),
        )
    )


def nonmana_activation_prohibited_by_chosen_name(
    host: ActivationRestrictionHost,
    source: Any,
    ability: ActivatedAbility,
) -> bool:
    return bool(current_activation_prohibitions(host, source, ability))


__all__ = [
    "ACTIVATION_PERMISSION_EVENT",
    "ATTACHED_ACTIVATION_PROHIBITION_FRAGMENT_ID",
    "CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID",
    "ActivationProhibition",
    "ActivationRestrictionContext",
    "ActivationRestrictionHost",
    "ActivationRestrictionRegistry",
    "ChosenNameNonmanaProhibitionHandler",
    "ChosenNameNonmanaRestrictionNode",
    "current_activation_prohibitions",
    "default_activation_restriction_registry",
    "nonmana_activation_prohibited_by_chosen_name",
]
