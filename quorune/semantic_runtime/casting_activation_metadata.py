from __future__ import annotations

"""Typed runtime metadata for bounded casting and activation rules."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..card_program_faces import program_matches_face
from ..characteristic_evaluation import type_parts
from ..creature_subtypes import canonical_creature_subtype
from ..continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionError, OwnerRelativeConditionSource
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, program_has_complete_card_program_admission
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .self_graveyard_discard import SelfGraveyardDiscardPermissionHandler
from ..rules.self_graveyard_discard import HANDLER_ID as SELF_GRAVEYARD_DISCARD_HANDLER_ID, SelfGraveyardDiscardSpec


SELF_ZONE_CAST_PERMISSION_EVENT = "cast.zone.permission"
SELF_ZONE_CAST_PERMISSION_HANDLER_ID = (
    "permission.cast.self-zone-controlled-subtype.v1"
)
SELF_PUBLIC_ZONE_CAST_PERMISSION_HANDLER_ID = 'permission.cast.self-public-zones.v1'
SELF_PUBLIC_ZONE_CAST_PERMISSION_CAPABILITY = 'casting.zone.self_public_repeatable'
LOYALTY_COST_MODIFIER_EVENT = "activation.cost.modify"
LOYALTY_COST_MODIFIER_HANDLER_ID = "modification.activation.loyalty-cost.v1"


@dataclass(frozen=True, slots=True)
class SelfZoneCastPermission:
    source_zone: str
    controlled_subtypes_any: tuple[str, ...]
    source_condition: FixedPublicStateConditionSpec | None = None
    unconditional: bool = False


@dataclass(frozen=True, slots=True)
class LoyaltyCostModifier:
    affected_controller: str
    adjustment_kind: str
    amount: int
    source_ref: str
    source_controller: str


@dataclass(frozen=True, slots=True)
class LoyaltyCostModifierSourceContext:
    source_ref: str
    source_controller: str


@dataclass(frozen=True, slots=True)
class SelfZoneCastPermissionHandler:
    handler_id: str = SELF_ZONE_CAST_PERMISSION_HANDLER_ID
    schema_version: int = 1
    family: str = "permission.cast.self-zone"
    event: str = SELF_ZONE_CAST_PERMISSION_EVENT
    rule_references: tuple[str, ...] = ("205.3m", "601.3")
    capability_dependencies: tuple[str, ...] = (
        "casting.zone.self_graveyard.controlled_subtype",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> SelfZoneCastPermission:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "source_zone",
                "controlled_subtypes_any",
            },
            field="Self-zone cast-permission handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Self-zone cast-permission handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported self-zone cast-permission schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Self-zone cast-permission handler must use {self.event}"
            )
        if descriptor["source_zone"] != "graveyard":
            raise SemanticNodeError(
                "Self-zone cast permission supports only the graveyard"
            )
        raw_subtypes = descriptor["controlled_subtypes_any"]
        if (
            not isinstance(raw_subtypes, list)
            or len(raw_subtypes) != 1
            or canonical_creature_subtype(raw_subtypes[0]) != raw_subtypes[0]
        ):
            raise SemanticNodeError(
                "Self-zone cast permission requires one canonical creature subtype"
            )
        return SelfZoneCastPermission(
            source_zone="graveyard",
            controlled_subtypes_any=(str(raw_subtypes[0]),),
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[SelfZoneCastPermission, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class PublicStateSelfZoneCastPermissionHandler(SelfZoneCastPermissionHandler):
    handler_id: str = 'permission.cast.self-zone-public-state.v1'
    schema_version: int = 2
    capability_dependencies: tuple[str, ...] = (
        'casting.zone.self_graveyard.controlled_subtype','continuous.characteristics.fixed_public_state',
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id','schema_version','event','source_zone','source_condition'},
            field='Public-state self-zone cast permission')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=self.schema_version or descriptor['event']!=self.event
                or descriptor['source_zone']!='graveyard'):
            raise SemanticNodeError('Public-state self-zone permission identity changed')
        try:
            condition=FixedPublicStateConditionSpec.from_dict(descriptor['source_condition'])
        except (FixedPublicStateConditionError,TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return SelfZoneCastPermission('graveyard',(),condition)


@dataclass(frozen=True, slots=True)
class SelfPublicZoneCastPermissionHandler:
    handler_id: str = SELF_PUBLIC_ZONE_CAST_PERMISSION_HANDLER_ID
    schema_version: int = 1
    family: str = 'permission.cast.self-zone'
    event: str = SELF_ZONE_CAST_PERMISSION_EVENT
    rule_references: tuple[str, ...] = ('113.6f', '117.1a', '601.2a', '601.2f', '601.2h', '601.3')
    capability_dependencies: tuple[str, ...] = (SELF_PUBLIC_ZONE_CAST_PERMISSION_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_zones',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD}, field='Self public-zone casting permission')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Self public-zone casting permission identity changed')
        zones = descriptor['source_zones']
        if (not isinstance(zones, list) or not 1 <= len(zones) <= 2
                or any(type(zone) is not str or zone not in {'graveyard', 'exile'} for zone in zones)
                or zones != sorted(set(zones))):
            raise SemanticNodeError('Self casting requires distinct canonical public source zones')
        return tuple(SelfZoneCastPermission(zone, (), unconditional=True) for zone in zones)

    def lower(self, descriptor, context):
        del context
        return self.validate(descriptor)


@dataclass(frozen=True, slots=True)
class LoyaltyCostModifierHandler:
    handler_id: str = LOYALTY_COST_MODIFIER_HANDLER_ID
    schema_version: int = 1
    family: str = "modification.activation.loyalty-cost"
    event: str = LOYALTY_COST_MODIFIER_EVENT
    rule_references: tuple[str, ...] = ("606.4", "606.6")
    capability_dependencies: tuple[str, ...] = (
        "activation.loyalty_cost.modifier_detection",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> tuple[str, str, int]:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "affected_controller",
                "adjustment_kind",
                "amount",
            },
            field="Loyalty-cost modifier handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Loyalty-cost modifier handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported loyalty-cost modifier schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Loyalty-cost modifier handler must use {self.event}"
            )
        affected_controller = descriptor["affected_controller"]
        if affected_controller not in {"source_controller", "opponent"}:
            raise SemanticNodeError(
                "Unsupported loyalty-cost affected-controller relation"
            )
        adjustment_kind = descriptor["adjustment_kind"]
        if adjustment_kind not in {"generic_mana_increase", "loyalty_increase"}:
            raise SemanticNodeError("Unsupported loyalty-cost adjustment kind")
        amount = descriptor["amount"]
        if type(amount) is not int or amount < 1:
            raise SemanticNodeError(
                "Loyalty-cost adjustment amount must be a positive integer"
            )
        return str(affected_controller), str(adjustment_kind), amount

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: LoyaltyCostModifierSourceContext,
    ) -> tuple[LoyaltyCostModifier, ...]:
        affected_controller, adjustment_kind, amount = self.validate(descriptor)
        return (
            LoyaltyCostModifier(
                affected_controller=affected_controller,
                adjustment_kind=adjustment_kind,
                amount=amount,
                source_ref=context.source_ref,
                source_controller=context.source_controller,
            ),
        )


class SelfZoneCastPermissionRegistry(
    RuntimeComponentRegistry[object, SelfZoneCastPermission | SelfGraveyardDiscardSpec]
):
    pass


class LoyaltyCostModifierRegistry(
    RuntimeComponentRegistry[
        LoyaltyCostModifierSourceContext,
        LoyaltyCostModifier,
    ]
):
    pass


@lru_cache(maxsize=1)
def default_self_zone_cast_permission_registry() -> SelfZoneCastPermissionRegistry:
    registry = SelfZoneCastPermissionRegistry((SelfZoneCastPermissionHandler(),PublicStateSelfZoneCastPermissionHandler(),
        SelfPublicZoneCastPermissionHandler(),SelfGraveyardDiscardPermissionHandler()))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


@lru_cache(maxsize=1)
def default_loyalty_cost_modifier_registry() -> LoyaltyCostModifierRegistry:
    registry = LoyaltyCostModifierRegistry((LoyaltyCostModifierHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


class CastingActivationMetadataHost(Protocol):
    semantics: Any

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> Sequence[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _effective_static_component_keys(self, card: Any) -> Sequence[str]: ...

    def _fixed_public_state_condition_holds(self, source: Any, condition: FixedPublicStateConditionSpec,
        *, require_available: bool = False) -> bool: ...


def compiled_self_zone_cast_permission(
    host: CastingActivationMetadataHost,
    seat: str,
    card: Any,
) -> bool:
    """Evaluate one card's trusted, face-pinned self-zone permission."""

    if card.owner != seat or card.zone not in {'graveyard', 'exile'} or card.face_down or card.object_kind != 'card':
        return False
    record = host.card_record(card)
    if record is None:
        return False
    registry = default_self_zone_cast_permission_registry()
    permissions: list[SelfZoneCastPermission] = []
    programs = (program for zone in (card.zone, 'all') for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id, active_zone=zone, event=SELF_ZONE_CAST_PERMISSION_EVENT))
    current_components = None
    for program in programs:
        if (
            not host.semantic_program_is_current_trusted(program)
            or not program_matches_face(record, program, card)
        ):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == SELF_GRAVEYARD_DISCARD_HANDLER_ID:
                continue
            if descriptor.get('handler_id') == SELF_PUBLIC_ZONE_CAST_PERMISSION_HANDLER_ID:
                if not program_has_complete_card_program_admission(program):
                    continue
                if current_components is None:
                    current_components = frozenset(host._effective_static_component_keys(card))
                if program.key not in current_components:
                    continue
            if registry.describe(str(descriptor.get("handler_id") or "")):
                permissions.extend(registry.lower(descriptor, None))
    for permission in permissions:
        if permission.source_zone != card.zone:
            continue
        if permission.unconditional:
            return True
        if permission.source_condition is not None:
            if host._fixed_public_state_condition_holds(OwnerRelativeConditionSource(card),
                    permission.source_condition, require_available=True):
                return True
            continue
        if any(
            candidate.zone == "battlefield"
            and candidate.controller == seat
            and not candidate.phased_out
            and bool(
                set(permission.controlled_subtypes_any).intersection(
                    type_parts(
                        str(
                            host._effective_card_data(candidate).get("type_line")
                            or ""
                        )
                    )[1]
                )
            )
            for candidate in host._semantic_event_sources(zones={"battlefield"})
        ):
            return True
    return False


def active_loyalty_cost_modifiers(
    host: CastingActivationMetadataHost,
) -> tuple[LoyaltyCostModifier, ...]:
    """Collect visible trusted loyalty-cost modifiers without reading prose."""

    registry = default_loyalty_cost_modifier_registry()
    modifiers: list[LoyaltyCostModifier] = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.zone != "battlefield" or source.phased_out:
            continue
        record = host.card_record(source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone="battlefield",
            event=LOYALTY_COST_MODIFIER_EVENT,
        ):
            if (
                not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, source)
            ):
                continue
            context = LoyaltyCostModifierSourceContext(
                source_ref=source.ref,
                source_controller=source.controller,
            )
            for descriptor in program.handlers:
                if registry.describe(str(descriptor.get("handler_id") or "")):
                    modifiers.extend(registry.lower(descriptor, context))
    return tuple(
        sorted(
            modifiers,
            key=lambda value: (
                value.source_ref,
                value.affected_controller,
                value.adjustment_kind,
                value.amount,
            ),
        )
    )


__all__ = [
    "LOYALTY_COST_MODIFIER_EVENT",
    "LOYALTY_COST_MODIFIER_HANDLER_ID",
    "SELF_ZONE_CAST_PERMISSION_EVENT",
    "SELF_ZONE_CAST_PERMISSION_HANDLER_ID",
    "LoyaltyCostModifier",
    "LoyaltyCostModifierHandler",
    "LoyaltyCostModifierRegistry",
    "SelfZoneCastPermission",
    "SelfZoneCastPermissionHandler",
    "SelfZoneCastPermissionRegistry",
    "active_loyalty_cost_modifiers",
    "compiled_self_zone_cast_permission",
    "default_loyalty_cost_modifier_registry",
    "default_self_zone_cast_permission_registry",
]
