from __future__ import annotations

"""Typed static restrictions on activating abilities."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    activation_prohibition_specs,
)
from ..abilities import ActivatedAbility
from ..attachments import attached_player_seat
from ..card_program_faces import program_matches_face
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import ObjectQueryResult, object_matches_query
from ..rules.capabilities import load_default_capability_registry
from ..untap_step import (
    UntapInstruction,
    UntapStepParticipation,
    UntapSubjectRelation,
    UntapTurnRelation,
)
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .current_ability_components import program_has_current_ability_fragments


ACTIVATION_PERMISSION_EVENT = "activation.permission"
CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID = (
    "restriction.activation.chosen-name-nonmana.v1"
)
ATTACHED_ACTIVATION_PROHIBITION_FRAGMENT_ID = (
    "ability.fragment.activation-prohibition.v1"
)
FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID = (
    "restriction.activation.fixed-public.v1"
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
    restriction_source_controller: str
    restriction_attached_object_id: str | None
    restriction_attached_player: str | None
    candidate_row: ObjectQueryResult
    candidate_is_loyalty_ability: bool

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
        if type(self.candidate_is_loyalty_ability) is not bool:
            raise SemanticNodeError(
                "Activation restriction loyalty status must be boolean"
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


@dataclass(frozen=True, slots=True)
class FixedPublicActivationProhibitionHandler:
    handler_id: str = FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID
    schema_version: int = 1
    family: str = "restriction.activation.fixed_public"
    event: str = ACTIVATION_PERMISSION_EVENT
    rule_references: tuple[str, ...] = ("101.2", "602.1", "602.5", "605.1a")
    capability_dependencies: tuple[str, ...] = (
        "activation.restriction.fixed_public_query",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "subject_relation",
                "controller_relation",
                "ability_scope",
                "predicate",
                "prevents_untap",
            },
            field="Fixed public activation prohibition",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Activation-prohibition handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported fixed activation-prohibition schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Fixed activation prohibitions must use {self.event}"
            )
        relation = descriptor["subject_relation"]
        if relation not in {
            "query",
            "enchanted_object",
            "enchanted_player",
            "chosen_name",
        }:
            raise SemanticNodeError("Activation-prohibition subject is unsupported")
        controller_relation = descriptor["controller_relation"]
        if controller_relation not in {"any", "source_opponents"}:
            raise SemanticNodeError(
                "Activation-prohibition controller relation is unsupported"
            )
        ability_scope = descriptor["ability_scope"]
        if ability_scope not in {
            "all",
            "nonmana",
            "loyalty",
            "nonmana_nonloyalty",
        }:
            raise SemanticNodeError("Activation-prohibition scope is unsupported")
        try:
            predicate = ObjectQuerySpec.from_dict(descriptor["predicate"])
        except (ObjectQueryError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if type(descriptor["prevents_untap"]) is not bool:
            raise SemanticNodeError(
                "Activation-prohibition untap participation must be boolean"
            )
        if descriptor["prevents_untap"] and relation != "enchanted_object":
            raise SemanticNodeError(
                "Untap participation requires the enchanted object"
            )
        if relation == "query" and predicate.zones not in {
            (),
            ("battlefield",),
        }:
            raise SemanticNodeError(
                "Fixed activation queries apply only on the battlefield"
            )
        if relation != "query" and any(
            (
                predicate.types_all,
                predicate.types_any,
                predicate.subtypes_all,
                predicate.subtypes_any,
            )
        ):
            raise SemanticNodeError(
                "Pinned activation-prohibition subjects reserve predicates"
            )
        return {
            "relation": relation,
            "controller_relation": controller_relation,
            "ability_scope": ability_scope,
            "predicate": predicate,
            "prevents_untap": descriptor["prevents_untap"],
        }

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ActivationRestrictionContext,
    ) -> tuple[ActivationProhibition, ...]:
        node = self.validate(descriptor)
        if node["ability_scope"] == "nonmana" and context.candidate_is_mana_ability:
            return ()
        if node["ability_scope"] == "loyalty" and not context.candidate_is_loyalty_ability:
            return ()
        if node["ability_scope"] == "nonmana_nonloyalty" and (
            context.candidate_is_mana_ability
            or context.candidate_is_loyalty_ability
        ):
            return ()
        if (
            node["controller_relation"] == "source_opponents"
            and context.candidate_row.controller
            == context.restriction_source_controller
        ):
            return ()
        relation = node["relation"]
        applies = bool(
            (
                relation == "query"
                and context.candidate_row.zone == "battlefield"
                and object_matches_query(context.candidate_row, node["predicate"])
            )
            or (
                relation == "enchanted_object"
                and context.restriction_attached_object_id
                == context.candidate_row.object_id
            )
            or (
                relation == "enchanted_player"
                and context.restriction_attached_player
                == context.candidate_row.controller
            )
            or (
                relation == "chosen_name"
                and context.chosen_name
                and context.chosen_name == context.candidate_source_name
            )
        )
        if not applies:
            return ()
        return (
            ActivationProhibition(
                restriction_source_ref=context.restriction_source_ref,
                candidate_source_name=context.candidate_source_name,
                handler_id=self.handler_id,
                reason="fixed_public_activation_prohibition",
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
        (
            ChosenNameNonmanaProhibitionHandler(),
            FixedPublicActivationProhibitionHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


class ActivationRestrictionHost(Protocol):
    active_seats: Sequence[str]
    state: Any
    semantics: Any

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> Sequence[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _public_object_query_result(self, card: Any) -> ObjectQueryResult: ...

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
    candidate_row = host._public_object_query_result(source)
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
                or (
                    CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                    and not program_has_current_ability_fragments(
                        program,
                        host._effective_card_data(restriction_source),
                    )
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
                restriction_source_controller=restriction_source.controller,
                restriction_attached_object_id=restriction_source.attached_to,
                restriction_attached_player=attached_player_seat(
                    restriction_source
                ),
                candidate_row=candidate_row,
                candidate_is_loyalty_ability=(ability.loyalty_delta is not None),
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


def attached_activation_untap_participations(
    host: ActivationRestrictionHost,
) -> tuple[UntapStepParticipation, ...]:
    """Materialize the untap half of exact attached compound restraints."""

    result: list[UntapStepParticipation] = []
    for restriction_source in host._semantic_event_sources(
        zones={"battlefield"}
    ):
        if (
            restriction_source.zone != "battlefield"
            or restriction_source.phased_out
            or restriction_source.attached_to not in host.state.cards
        ):
            continue
        record = host.card_record(restriction_source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(
            restriction_source.oracle_id,
            active_zone="battlefield",
            event=ACTIVATION_PERMISSION_EVENT,
        ):
            if (
                not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, restriction_source)
                or (
                    CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                    and not program_has_current_ability_fragments(
                        program,
                        host._effective_card_data(restriction_source),
                    )
                )
            ):
                continue
            for index, descriptor in enumerate(program.handlers):
                if (
                    descriptor.get("handler_id")
                    != FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID
                ):
                    continue
                node = FixedPublicActivationProhibitionHandler().validate(
                    descriptor
                )
                if not node["prevents_untap"]:
                    continue
                result.append(
                    UntapStepParticipation(
                        participation_id=(
                            f"{FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID}:"
                            f"{restriction_source.logical_object_id}:"
                            f"{program.key}:{index}"
                        ),
                        source_object_id=restriction_source.object_id,
                        source_ref=restriction_source.ref,
                        source_controller=restriction_source.controller,
                        instruction=UntapInstruction.PROHIBIT,
                        subject_relation=UntapSubjectRelation.ATTACHED_OBJECT,
                        turn_relation=UntapTurnRelation.SUBJECT_CONTROLLER,
                        predicate=ObjectQuerySpec(zones=("battlefield",)),
                        subject_object_id=restriction_source.attached_to,
                    )
                )
    return tuple(
        sorted(result, key=lambda participation: participation.participation_id)
    )


__all__ = [
    "ACTIVATION_PERMISSION_EVENT",
    "ATTACHED_ACTIVATION_PROHIBITION_FRAGMENT_ID",
    "CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID",
    "FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID",
    "ActivationProhibition",
    "ActivationRestrictionContext",
    "ActivationRestrictionHost",
    "ActivationRestrictionRegistry",
    "ChosenNameNonmanaProhibitionHandler",
    "ChosenNameNonmanaRestrictionNode",
    "FixedPublicActivationProhibitionHandler",
    "current_activation_prohibitions",
    "attached_activation_untap_participations",
    "default_activation_restriction_registry",
    "nonmana_activation_prohibited_by_chosen_name",
]
