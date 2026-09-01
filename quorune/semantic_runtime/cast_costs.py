from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..card_program_faces import program_matches_face
from ..casting_payment_keywords import (
    AffinitySpec,
    CastingPaymentKeywordError,
    DelveSpec,
    ImproviseSpec,
)
from ..convoke import ConvokeError, ConvokeSpec
from ..evoke import (
    EVOKE_CAPABILITY_ID,
    EVOKE_HANDLER_ID,
    EVOKE_RUNTIME_EVENT,
    EvokeError,
    FixedManaEvokeSpec,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from ..self_cast_reductions import (
    SelfCastReductionError,
    SelfSpellCostReductionSpec,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


CONVOKE_ACTIVE_ZONE = "stack"
CONVOKE_COST_EVENT = "cast.cost"
CONVOKE_HANDLER_ID = "casting.payment.convoke.v1"
AFFINITY_HANDLER_ID = "casting.payment.affinity-artifacts.v1"
TYPED_AFFINITY_HANDLER_ID = "casting.payment.affinity.v2"
DELVE_HANDLER_ID = "casting.payment.delve.v1"
IMPROVISE_HANDLER_ID = "casting.payment.improvise.v1"
FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID = (
    "casting.cost.modifier.fixed_query"
)
FIXED_SPELL_COST_REDUCTION_EVENT = "cast.cost.modify"
FIXED_SPELL_COST_REDUCTION_HANDLER_ID = (
    "modification.cast-cost.fixed-query.v1"
)
SELF_SPELL_COST_REDUCTION_CAPABILITY_ID = (
    "casting.cost.self_public_reduction"
)
SELF_SPELL_COST_REDUCTION_HANDLER_ID = (
    "modification.cast-cost.self-public.v1"
)


@dataclass(frozen=True, slots=True)
class FixedSpellCostReductionSpec:
    predicate: ObjectQuerySpec
    generic_reduction: int


@dataclass(frozen=True, slots=True)
class ConvokeCostHandler:
    handler_id: str = CONVOKE_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.payment.convoke"
    event: str = CONVOKE_COST_EVENT
    rule_references: tuple[str, ...] = (
        "601.2f",
        "601.2g",
        "601.2h",
        "601.2i",
        "702.51",
        "702.51a",
        "702.51b",
        "702.51c",
        "702.51d",
    )
    capability_dependencies: tuple[str, ...] = ("casting.payment.convoke",)

    def validate(self, descriptor: Mapping[str, Any]) -> ConvokeSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "payment"},
            field="Convoke cost handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Convoke cost handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError("Unsupported Convoke cost handler version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Convoke cost handler must use {self.event}"
            )
        payment = descriptor["payment"]
        if not isinstance(payment, Mapping):
            raise SemanticNodeError("Convoke payment descriptor must be an object")
        try:
            return ConvokeSpec.from_dict(payment)
        except ConvokeError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[ConvokeSpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class AffinityCostHandler:
    handler_id: str = AFFINITY_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.payment.affinity_artifacts"
    event: str = CONVOKE_COST_EVENT
    rule_references: tuple[str, ...] = (
        "601.2f",
        "601.2g",
        "601.2h",
        "702.41",
        "702.41a",
        "702.41b",
    )
    capability_dependencies: tuple[str, ...] = (
        "casting.payment.affinity_artifacts",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> AffinitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "payment"},
            field="Affinity cost handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Affinity cost handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError("Unsupported Affinity cost handler version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Affinity cost handler must use {self.event}"
            )
        payment = descriptor["payment"]
        if not isinstance(payment, Mapping):
            raise SemanticNodeError("Affinity payment descriptor must be an object")
        exact_fields(
            payment,
            {"schema_version", "kind", "card_type"},
            field="Affinity payment descriptor",
        )
        if (
            type(payment["schema_version"]) is not int
            or payment["schema_version"] != 1
        ):
            raise SemanticNodeError("Unsupported Affinity payment version")
        if payment["kind"] != "affinity" or payment["card_type"] != "artifact":
            raise SemanticNodeError(
                "Affinity payment must be the closed artifact-count family"
            )
        return AffinitySpec.for_quality("artifacts")

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[AffinitySpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class TypedAffinityCostHandler:
    handler_id: str = TYPED_AFFINITY_HANDLER_ID
    schema_version: int = 2
    family: str = "casting.payment.affinity"
    event: str = CONVOKE_COST_EVENT
    rule_references: tuple[str, ...] = (
        "601.2f",
        "601.2g",
        "601.2h",
        "702.41",
        "702.41a",
        "702.41b",
    )
    capability_dependencies: tuple[str, ...] = ("casting.payment.affinity",)

    def validate(self, descriptor: Mapping[str, Any]) -> AffinitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "payment"},
            field="typed Affinity cost handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Typed Affinity handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError("Unsupported typed Affinity handler version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Typed Affinity handler must use {self.event}"
            )
        try:
            return AffinitySpec.from_dict(descriptor["payment"])
        except (CastingPaymentKeywordError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[AffinitySpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class ImproviseCostHandler:
    handler_id: str = IMPROVISE_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.payment.improvise"
    event: str = CONVOKE_COST_EVENT
    rule_references: tuple[str, ...] = (
        "601.2f",
        "601.2g",
        "601.2h",
        "601.2i",
        "702.126",
        "702.126a",
        "702.126b",
    )
    capability_dependencies: tuple[str, ...] = ("casting.payment.improvise",)

    def validate(self, descriptor: Mapping[str, Any]) -> ImproviseSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "payment"},
            field="Improvise cost handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Improvise handler identity, version, or event changed"
            )
        try:
            return ImproviseSpec.from_dict(descriptor["payment"])
        except (CastingPaymentKeywordError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self, descriptor: Mapping[str, Any], context: object
    ) -> tuple[ImproviseSpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class DelveCostHandler:
    handler_id: str = DELVE_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.payment.delve"
    event: str = CONVOKE_COST_EVENT
    rule_references: tuple[str, ...] = (
        "601.2f",
        "601.2g",
        "601.2h",
        "702.66",
        "702.66a",
        "702.66b",
    )
    capability_dependencies: tuple[str, ...] = ("casting.payment.delve",)

    def validate(self, descriptor: Mapping[str, Any]) -> DelveSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "payment"},
            field="Delve cost handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Delve handler identity, version, or event changed"
            )
        try:
            return DelveSpec.from_dict(descriptor["payment"])
        except (CastingPaymentKeywordError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self, descriptor: Mapping[str, Any], context: object
    ) -> tuple[DelveSpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class EvokeCostHandler:
    handler_id: str = EVOKE_HANDLER_ID
    schema_version: int = 1
    family: str = EVOKE_CAPABILITY_ID
    event: str = EVOKE_RUNTIME_EVENT
    rule_references: tuple[str, ...] = (
        "601.2f",
        "601.2g",
        "601.2h",
        "702.74",
        "702.74a",
    )
    capability_dependencies: tuple[str, ...] = (EVOKE_CAPABILITY_ID,)

    def validate(self, descriptor: Mapping[str, Any]) -> FixedManaEvokeSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "evoke"},
            field="fixed-mana Evoke handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Evoke handler identity, version, or event changed"
            )
        try:
            return FixedManaEvokeSpec.from_dict(descriptor["evoke"])
        except (EvokeError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self, descriptor: Mapping[str, Any], context: object
    ) -> tuple[FixedManaEvokeSpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class FixedSpellCostReductionHandler:
    handler_id: str = FIXED_SPELL_COST_REDUCTION_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.cost.modifier.fixed_query"
    event: str = FIXED_SPELL_COST_REDUCTION_EVENT
    rule_references: tuple[str, ...] = ("601.2f", "601.2h")
    capability_dependencies: tuple[str, ...] = (
        FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID,
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> FixedSpellCostReductionSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "affected_controller",
                "predicate",
                "generic_reduction",
            },
            field="fixed spell-cost reduction handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Spell-cost reduction identity, version, or event changed"
            )
        if descriptor["affected_controller"] != "source_controller":
            raise SemanticNodeError(
                "Spell-cost reductions support only the source controller"
            )
        amount = descriptor["generic_reduction"]
        if type(amount) is not int or amount < 1:
            raise SemanticNodeError(
                "Spell-cost reduction must be a positive generic amount"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(descriptor["predicate"])
        except (ObjectQueryError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (
            predicate.zones
            or predicate.owner is not None
            or predicate.controller is not None
            or predicate.keywords_all
            or predicate.token is not None
            or predicate.tapped is not None
            or predicate.include_phased_out
            or predicate.known_to_actor is not None
            or predicate.exclude_ref is not None
            or predicate.state_predicate is not None
        ):
            raise SemanticNodeError(
                "Spell-cost reductions require a fixed characteristic predicate"
            )
        return FixedSpellCostReductionSpec(predicate, amount)

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedSpellCostReductionSpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class SelfSpellCostReductionHandler:
    handler_id: str = SELF_SPELL_COST_REDUCTION_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.cost.modifier.self_public"
    event: str = FIXED_SPELL_COST_REDUCTION_EVENT
    rule_references: tuple[str, ...] = ("601.2f", "601.2h")
    capability_dependencies: tuple[str, ...] = (
        SELF_SPELL_COST_REDUCTION_CAPABILITY_ID,
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> SelfSpellCostReductionSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "reduction"},
            field="self spell-cost reduction handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Self spell-cost reduction identity, version, or event changed"
            )
        try:
            return SelfSpellCostReductionSpec.from_dict(
                descriptor["reduction"]
            )
        except (SelfCastReductionError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[SelfSpellCostReductionSpec, ...]:
        del context
        return (self.validate(descriptor),)


class CastCostComponentRegistry(
    RuntimeComponentRegistry[
        object,
        ConvokeSpec
        | AffinitySpec
        | ImproviseSpec
        | DelveSpec
        | FixedManaEvokeSpec
        | FixedSpellCostReductionSpec
        | SelfSpellCostReductionSpec,
    ]
):
    pass


@lru_cache(maxsize=1)
def default_cast_cost_component_registry() -> CastCostComponentRegistry:
    registry = CastCostComponentRegistry(
        (
            AffinityCostHandler(),
            ConvokeCostHandler(),
            DelveCostHandler(),
            EvokeCostHandler(),
            FixedSpellCostReductionHandler(),
            ImproviseCostHandler(),
            SelfSpellCostReductionHandler(),
            TypedAffinityCostHandler(),
        )
    )
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def convoke_handler_descriptor() -> dict[str, Any]:
    return {
        "handler_id": CONVOKE_HANDLER_ID,
        "schema_version": 1,
        "event": CONVOKE_COST_EVENT,
        "payment": ConvokeSpec().to_dict(),
    }


def affinity_handler_descriptor(
    spec: AffinitySpec | None = None,
) -> dict[str, Any]:
    if spec is not None:
        return {
            "handler_id": TYPED_AFFINITY_HANDLER_ID,
            "schema_version": 2,
            "event": CONVOKE_COST_EVENT,
            "payment": spec.to_dict(),
        }
    return {
        "handler_id": AFFINITY_HANDLER_ID,
        "schema_version": 1,
        "event": CONVOKE_COST_EVENT,
        "payment": {
            "schema_version": 1,
            "kind": "affinity",
            "card_type": "artifact",
        },
    }


def improvise_handler_descriptor() -> dict[str, Any]:
    return {
        "handler_id": IMPROVISE_HANDLER_ID,
        "schema_version": 1,
        "event": CONVOKE_COST_EVENT,
        "payment": ImproviseSpec().to_dict(),
    }


def delve_handler_descriptor() -> dict[str, Any]:
    return {
        "handler_id": DELVE_HANDLER_ID,
        "schema_version": 1,
        "event": CONVOKE_COST_EVENT,
        "payment": DelveSpec().to_dict(),
    }


def evoke_handler_descriptor(spec: FixedManaEvokeSpec) -> dict[str, Any]:
    return {
        "handler_id": EVOKE_HANDLER_ID,
        "schema_version": 1,
        "event": EVOKE_RUNTIME_EVENT,
        "evoke": spec.to_dict(),
    }


class FixedSpellCostReductionHost(Protocol):
    semantics: Any

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> Sequence[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self,
        type_line: str,
    ) -> tuple[set[str], set[str], set[str]]: ...

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def active_fixed_spell_cost_reductions(
    host: FixedSpellCostReductionHost,
    seat: str,
    card: Any,
    *,
    cast_type_line: str | None = None,
) -> tuple[FixedSpellCostReductionSpec, ...]:
    """Collect applicable trusted reducers at the casting boundary."""

    effective = dict(host._effective_card_data(card))
    if cast_type_line is not None:
        effective["type_line"] = cast_type_line
    row = object_query_result(
        card,
        effective,
        type_parts=host._type_parts(str(effective.get("type_line") or "")),
        known_to_actor=True,
        attached_to_ref=None,
    )
    registry = default_cast_cost_component_registry()
    reductions: list[FixedSpellCostReductionSpec] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if (
            source.zone != "battlefield"
            or source.controller != seat
            or source.phased_out
        ):
            continue
        record = host.card_record(source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone="battlefield",
            event=FIXED_SPELL_COST_REDUCTION_EVENT,
        ):
            if (
                not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, source)
            ):
                continue
            for descriptor in program.handlers:
                if (
                    descriptor.get("handler_id")
                    != FIXED_SPELL_COST_REDUCTION_HANDLER_ID
                ):
                    continue
                spec = registry.lower(descriptor, None)[0]
                assert isinstance(spec, FixedSpellCostReductionSpec)
                if object_matches_query(row, spec.predicate):
                    reductions.append(spec)
    return tuple(reductions)


__all__ = [
    "AFFINITY_HANDLER_ID",
    "AffinityCostHandler",
    "AffinitySpec",
    "CONVOKE_ACTIVE_ZONE",
    "CONVOKE_COST_EVENT",
    "CONVOKE_HANDLER_ID",
    "CastCostComponentRegistry",
    "ConvokeCostHandler",
    "DELVE_HANDLER_ID",
    "DelveCostHandler",
    "DelveSpec",
    "EvokeCostHandler",
    "FixedManaEvokeSpec",
    "FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID",
    "FIXED_SPELL_COST_REDUCTION_EVENT",
    "FIXED_SPELL_COST_REDUCTION_HANDLER_ID",
    "FixedSpellCostReductionHandler",
    "FixedSpellCostReductionSpec",
    "SELF_SPELL_COST_REDUCTION_CAPABILITY_ID",
    "SELF_SPELL_COST_REDUCTION_HANDLER_ID",
    "SelfSpellCostReductionHandler",
    "IMPROVISE_HANDLER_ID",
    "ImproviseCostHandler",
    "ImproviseSpec",
    "TYPED_AFFINITY_HANDLER_ID",
    "TypedAffinityCostHandler",
    "active_fixed_spell_cost_reductions",
    "affinity_handler_descriptor",
    "convoke_handler_descriptor",
    "delve_handler_descriptor",
    "default_cast_cost_component_registry",
    "evoke_handler_descriptor",
    "improvise_handler_descriptor",
]
