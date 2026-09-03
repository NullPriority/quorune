from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..replacement_effects import (
    PreventAmount,
    RedirectDamage,
    ReplacementClass,
    ReplacementEffect,
)
from ..damage_modifier_state import DamageModifierError, DamagePreventionScope
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


_QUANTITY_HANDLER_ID = "replacement.damage.quantity.v1"
_QUANTITY_V2_HANDLER_ID = "replacement.damage.quantity.v2"
_FIXED_PREVENTION_HANDLER_ID = "prevention.damage.fixed.v1"
_ALL_PREVENTION_HANDLER_ID = "prevention.damage.all.v1"
_STATIC_REDIRECTION_HANDLER_ID = "replacement.damage.redirect-to-source.v1"
_RELATIONS = {"any", "source_controller", "opponent"}
_TARGET_KINDS = {"player", "permanent"}


class DamageReplacementHost(Protocol):
    semantics: Any
    state: Any
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DamageReplacementCondition:
    source_controller_relation: str
    target_controller_relation: str
    target_kinds: tuple[str, ...]
    source_types_all: tuple[str, ...]
    target_types_all: tuple[str, ...]
    combat: bool | None
    source_types_any: tuple[str, ...] = ()
    source_colors_all: tuple[str, ...] = ()
    exclude_source_ref: bool = False


@dataclass(frozen=True, slots=True)
class DamageQuantityReplacementNode:
    condition: DamageReplacementCondition
    multiplier: int
    additional: int


@dataclass(frozen=True, slots=True)
class FixedDamagePreventionNode:
    condition: DamageReplacementCondition
    amount: int


@dataclass(frozen=True, slots=True)
class AllDamagePreventionScopeNode:
    damage_kind: str
    source_controller_turn_only: bool
    scope: DamagePreventionScope
    application_group: str | None = None


@dataclass(frozen=True, slots=True)
class AllDamagePreventionNode:
    scopes: tuple[AllDamagePreventionScopeNode, ...]


@dataclass(frozen=True, slots=True)
class StaticDamageRedirectionNode:
    condition: DamageReplacementCondition


@dataclass(frozen=True, slots=True)
class DamageReplacementSourceContext:
    source_ref: str
    source_controller: str
    component_id: str = ""
    source_destination: RedirectDamage | None = None
    attached_ref: str | None = None
    active_player: str | None = None

    def __post_init__(self) -> None:
        if not self.source_ref or not self.source_controller:
            raise SemanticNodeError(
                "Damage replacement sources require a ref and controller"
            )


def _normalized_strings(
    value: Any,
    *,
    field: str,
    allowed: set[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise SemanticNodeError(f"{field} must be a list of nonempty strings")
    result = tuple(" ".join(item.casefold().split()) for item in value)
    if len(result) != len(set(result)):
        raise SemanticNodeError(
            f"{field} must remain unique after normalization"
        )
    unknown = sorted(set(result) - allowed) if allowed is not None else []
    if unknown:
        raise SemanticNodeError(
            f"{field} contains unsupported values: {', '.join(unknown)}"
        )
    return result


def _condition(value: Any) -> DamageReplacementCondition:
    if not isinstance(value, Mapping):
        raise SemanticNodeError(
            "Damage replacement condition must be an object"
        )
    exact_fields(
        value,
        {
            "source_controller_relation",
            "target_controller_relation",
            "target_kinds",
            "source_types_all",
            "target_types_all",
            "combat",
        },
        field="damage replacement condition",
    )
    source_relation = str(value["source_controller_relation"])
    target_relation = str(value["target_controller_relation"])
    if source_relation not in _RELATIONS or target_relation not in _RELATIONS:
        raise SemanticNodeError(
            "Damage replacement relations must be any, source_controller, "
            "or opponent"
        )
    combat = value["combat"]
    if combat is not None and type(combat) is not bool:
        raise SemanticNodeError(
            "Damage replacement combat must be a boolean or null"
        )
    return DamageReplacementCondition(
        source_controller_relation=source_relation,
        target_controller_relation=target_relation,
        target_kinds=_normalized_strings(
            value["target_kinds"],
            field="condition.target_kinds",
            allowed=_TARGET_KINDS,
        ),
        source_types_all=_normalized_strings(
            value["source_types_all"],
            field="condition.source_types_all",
        ),
        target_types_all=_normalized_strings(
            value["target_types_all"],
            field="condition.target_types_all",
        ),
        combat=combat,
    )


def _condition_v2(value: Any) -> DamageReplacementCondition:
    if not isinstance(value, Mapping):
        raise SemanticNodeError(
            "Damage replacement condition must be an object"
        )
    exact_fields(
        value,
        {
            "source_controller_relation",
            "target_controller_relation",
            "target_kinds",
            "source_types_all",
            "source_types_any",
            "source_colors_all",
            "target_types_all",
            "combat",
            "exclude_source_ref",
        },
        field="damage replacement condition",
    )
    base = _condition(
        {
            field: value[field]
            for field in (
                "source_controller_relation",
                "target_controller_relation",
                "target_kinds",
                "source_types_all",
                "target_types_all",
                "combat",
            )
        }
    )
    source_types_any = _normalized_strings(
        value["source_types_any"], field="condition.source_types_any"
    )
    source_colors_all = tuple(
        color.upper()
        for color in _normalized_strings(
            value["source_colors_all"],
            field="condition.source_colors_all",
            allowed=set("wubrgc"),
        )
    )
    exclude_source_ref = value["exclude_source_ref"]
    if type(exclude_source_ref) is not bool:
        raise SemanticNodeError(
            "Damage replacement exclude_source_ref must be a boolean"
        )
    if base.source_types_all and source_types_any:
        raise SemanticNodeError(
            "Damage replacement cannot combine all and any source types"
        )
    return DamageReplacementCondition(
        source_controller_relation=base.source_controller_relation,
        target_controller_relation=base.target_controller_relation,
        target_kinds=base.target_kinds,
        source_types_all=base.source_types_all,
        target_types_all=base.target_types_all,
        combat=base.combat,
        source_types_any=source_types_any,
        source_colors_all=source_colors_all,
        exclude_source_ref=exclude_source_ref,
    )


def _relation_predicate(
    relation: str,
    source_controller: str,
) -> Mapping[str, Any] | None:
    if relation == "any":
        return None
    if relation == "source_controller":
        return {"eq": source_controller}
    return {"not_in": [source_controller, None]}


def _event_conditions(
    condition: DamageReplacementCondition,
    context: DamageReplacementSourceContext,
) -> dict[str, Any]:
    # CR 120.8/614.7a: once prevention reduces the amount to zero, there is no
    # damage event left for another replacement or prevention effect to modify.
    # Unpreventable damage remains positive, so CR 615.12 still applies every
    # applicable prevention effect once without reducing the amount.
    result: dict[str, Any] = {"amount": {"not_in": [0]}}
    source = _relation_predicate(
        condition.source_controller_relation,
        context.source_controller,
    )
    if source is not None:
        result["source_controller"] = source
    target = _relation_predicate(
        condition.target_controller_relation,
        context.source_controller,
    )
    if target is not None:
        result["target_controller"] = target
    if condition.target_kinds:
        result["target_kind"] = {"in": list(condition.target_kinds)}
    if condition.source_types_all:
        result["source_characteristics"] = {
            "contains_all": list(condition.source_types_all)
        }
    if condition.source_types_any:
        result["source_characteristics"] = {
            "contains_any": list(condition.source_types_any)
        }
    if condition.source_colors_all:
        result["source_colors"] = {
            "contains_all": list(condition.source_colors_all)
        }
    if condition.exclude_source_ref:
        result["source"] = {"not_in": [context.source_ref]}
    if condition.target_types_all:
        result["target_characteristics"] = {
            "contains_all": list(condition.target_types_all)
        }
    if condition.combat is not None:
        result["combat"] = {"eq": condition.combat}
    return result


def _validate_envelope(
    descriptor: Mapping[str, Any],
    *,
    handler_id: str,
    schema_version: int = 1,
) -> None:
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
    if descriptor["handler_id"] != handler_id:
        raise SemanticNodeError("Runtime handler ID does not match registry")
    if descriptor["schema_version"] != schema_version:
        raise SemanticNodeError(f"Unsupported {handler_id} schema version")
    if descriptor["event"] != "damage":
        raise SemanticNodeError(f"{handler_id} must handle damage")


@dataclass(frozen=True, slots=True)
class DamageQuantityReplacementHandler:
    handler_id: str = _QUANTITY_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.damage.quantity"
    event: str = "damage"
    rule_references: tuple[str, ...] = (
        "120.4b",
        "614.1",
        "614.5",
        "616.1",
        "616.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "damage.replacement.static_quantity",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DamageQuantityReplacementNode:
        _validate_envelope(descriptor, handler_id=self.handler_id)
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Damage quantity modification must be an object"
            )
        exact_fields(
            modification,
            {"multiplier", "additional"},
            field="damage quantity modification",
        )
        multiplier = modification["multiplier"]
        additional = modification["additional"]
        if type(multiplier) is not int or multiplier < 1:
            raise SemanticNodeError(
                "Damage multiplier must be a positive integer"
            )
        if type(additional) is not int or additional < 0:
            raise SemanticNodeError(
                "Additional damage must be a nonnegative integer"
            )
        if multiplier == 1 and additional == 0:
            raise SemanticNodeError(
                "A damage replacement must change the amount"
            )
        return DamageQuantityReplacementNode(
            condition=_condition(descriptor["condition"]),
            multiplier=multiplier,
            additional=additional,
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
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
            conditions=_event_conditions(node.condition, context),
            operations=tuple(operations),
            label=f"{context.source_ref}: change damage amount",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


@dataclass(frozen=True, slots=True)
class DamageQuantityReplacementV2Handler(DamageQuantityReplacementHandler):
    """Fixed additive damage with typed color, OR-type, and self exclusions."""

    handler_id: str = _QUANTITY_V2_HANDLER_ID
    schema_version: int = 2

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DamageQuantityReplacementNode:
        _validate_envelope(
            descriptor,
            handler_id=self.handler_id,
            schema_version=self.schema_version,
        )
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Damage quantity modification must be an object"
            )
        exact_fields(
            modification,
            {"multiplier", "additional"},
            field="damage quantity modification",
        )
        multiplier = modification["multiplier"]
        additional = modification["additional"]
        if type(multiplier) is not int or multiplier < 1:
            raise SemanticNodeError(
                "Damage multiplier must be a positive integer"
            )
        if type(additional) is not int or additional < 1:
            raise SemanticNodeError(
                "V2 additive damage must be a positive integer"
            )
        return DamageQuantityReplacementNode(
            condition=_condition_v2(descriptor["condition"]),
            multiplier=multiplier,
            additional=additional,
        )


@dataclass(frozen=True, slots=True)
class FixedDamagePreventionHandler:
    handler_id: str = _FIXED_PREVENTION_HANDLER_ID
    schema_version: int = 1
    family: str = "prevention.damage.fixed"
    event: str = "damage"
    rule_references: tuple[str, ...] = (
        "120.4b",
        "615.1",
        "615.6",
        "615.10",
        "615.12",
        "615.12a",
        "616.1",
        "616.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "damage.prevention.static_fixed",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedDamagePreventionNode:
        _validate_envelope(descriptor, handler_id=self.handler_id)
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Fixed prevention modification must be an object"
            )
        exact_fields(
            modification,
            {"amount"},
            field="fixed prevention modification",
        )
        amount = modification["amount"]
        if type(amount) is not int or amount < 1:
            raise SemanticNodeError(
                "Fixed prevention amount must be a positive integer"
            )
        return FixedDamagePreventionNode(
            condition=_condition(descriptor["condition"]),
            amount=amount,
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        component_id = context.component_id or str(node.amount)
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:{component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions=_event_conditions(node.condition, context),
            operations=({"op": "prevent", "amount": node.amount},),
            label=f"{context.source_ref}: prevent {node.amount} damage",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


@dataclass(frozen=True, slots=True)
class AllDamagePreventionHandler:
    """Lower current static all-damage prevention through the shared scope."""

    handler_id: str = _ALL_PREVENTION_HANDLER_ID
    schema_version: int = 1
    family: str = "prevention.damage.all"
    event: str = "damage"
    rule_references: tuple[str, ...] = (
        "120.4b",
        "615.1",
        "615.6",
        "615.10",
        "615.12",
        "615.12a",
        "616.1",
        "616.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "damage.prevention.persistent_amount",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> AllDamagePreventionNode:
        _validate_envelope(descriptor, handler_id=self.handler_id)
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "All-damage prevention modification must be an object"
            )
        exact_fields(
            modification,
            {"amount"},
            field="all-damage prevention modification",
        )
        if modification["amount"] != "all":
            raise SemanticNodeError(
                "All-damage prevention must prevent the complete event"
            )
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError(
                "All-damage prevention condition must be an object"
            )
        exact_fields(
            condition,
            {"scopes"},
            field="all-damage prevention condition",
        )
        raw_scopes = condition["scopes"]
        if not isinstance(raw_scopes, list) or not raw_scopes:
            raise SemanticNodeError(
                "All-damage prevention scopes must be a nonempty list"
            )
        scopes: list[AllDamagePreventionScopeNode] = []
        for raw in raw_scopes:
            if not isinstance(raw, Mapping):
                raise SemanticNodeError(
                    "All-damage prevention scope entry must be an object"
                )
            expected_fields = {
                "damage_kind",
                "source_controller_turn_only",
                "scope",
            }
            if "application_group" in raw:
                expected_fields.add("application_group")
            exact_fields(
                raw,
                expected_fields,
                field="all-damage prevention scope entry",
            )
            damage_kind = str(raw["damage_kind"])
            turn_only = raw["source_controller_turn_only"]
            if damage_kind not in {"any", "combat", "noncombat"}:
                raise SemanticNodeError(
                    "All-damage prevention kind is unsupported"
                )
            if type(turn_only) is not bool:
                raise SemanticNodeError(
                    "All-damage prevention turn scope must be boolean"
                )
            application_group = raw.get("application_group")
            if application_group is not None and (
                type(application_group) is not str
                or not application_group
            ):
                raise SemanticNodeError(
                    "All-damage prevention application group must be nonempty or null"
                )
            raw_scope = raw["scope"]
            if not isinstance(raw_scope, Mapping):
                raise SemanticNodeError(
                    "All-damage prevention applicability scope is malformed"
                )
            try:
                scope = DamagePreventionScope.from_dict(raw_scope)
            except DamageModifierError as exc:
                raise SemanticNodeError(str(exc)) from exc
            scopes.append(
                AllDamagePreventionScopeNode(
                    damage_kind=damage_kind,
                    source_controller_turn_only=turn_only,
                    scope=scope,
                    application_group=application_group,
                )
            )
        return AllDamagePreventionNode(scopes=tuple(scopes))

    @staticmethod
    def _resolved_ref(
        value: str | None,
        context: DamageReplacementSourceContext,
    ) -> str | None:
        if value is None:
            return None
        if value == "$source":
            return context.source_ref
        if value == "$attached":
            return context.attached_ref
        if value.startswith("$"):
            raise SemanticNodeError(
                "Static all-damage prevention has an unresolved reference"
            )
        return value

    def _resolved_scope(
        self,
        scope: DamagePreventionScope,
        context: DamageReplacementSourceContext,
    ) -> DamagePreventionScope | None:
        values = scope.to_dict()
        for field in (
            "source_ref",
            "target_ref",
            "excluded_source_ref",
            "excluded_target_ref",
        ):
            raw = values[field]
            resolved = self._resolved_ref(
                str(raw) if raw is not None else None,
                context,
            )
            if raw == "$attached" and resolved is None:
                return None
            values[field] = resolved
        try:
            return DamagePreventionScope.from_dict(values)
        except DamageModifierError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        node = self.validate(descriptor)
        effects: list[ReplacementEffect] = []
        for index, entry in enumerate(node.scopes):
            if (
                entry.source_controller_turn_only
                and context.active_player != context.source_controller
            ):
                continue
            scope = self._resolved_scope(entry.scope, context)
            if scope is None:
                continue
            conditions = scope.event_conditions(
                controller=context.source_controller
            )
            conditions["amount"] = {"not_in": [0]}
            if entry.damage_kind != "any":
                conditions["combat"] = {
                    "eq": entry.damage_kind == "combat"
                }
            component_id = context.component_id or "all"
            application_group_id = (
                f"{self.handler_id}:{context.source_ref}:{component_id}:"
                f"{entry.application_group}"
                if entry.application_group is not None
                else None
            )
            effects.append(
                ReplacementEffect(
                    effect_id=(
                        f"{self.handler_id}:{context.source_ref}:"
                        f"{component_id}:{index}"
                    ),
                    source_id=context.source_ref,
                    event_kind=self.event,
                    replacement_class=ReplacementClass.OTHER,
                    conditions=conditions,
                    operations=(PreventAmount(),),
                    label=f"{context.source_ref}: prevent all damage",
                    application_group_id=application_group_id,
                )
            )
        return tuple(effects)


@dataclass(frozen=True, slots=True)
class StaticDamageRedirectionHandler:
    """Redirect matching damage to the current battlefield source.

    This is a static replacement component, so it has no durable resource to
    consume. It is collected only while the source is still a current,
    damageable battlefield object.
    """

    handler_id: str = _STATIC_REDIRECTION_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.damage.redirection.static"
    event: str = "damage"
    rule_references: tuple[str, ...] = (
        "614.1",
        "614.9",
        "616.1",
        "616.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "damage.redirection.static_to_source",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> StaticDamageRedirectionNode:
        _validate_envelope(descriptor, handler_id=self.handler_id)
        modification = descriptor["modification"]
        if not isinstance(modification, Mapping):
            raise SemanticNodeError(
                "Static damage redirection modification must be an object"
            )
        exact_fields(
            modification,
            {"destination"},
            field="static damage redirection modification",
        )
        if modification["destination"] != "source":
            raise SemanticNodeError(
                "Static damage redirection only supports its source"
            )
        return StaticDamageRedirectionNode(
            condition=_condition(descriptor["condition"]),
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        if context.source_destination is None:
            raise SemanticNodeError(
                "Static damage redirection requires a current damageable "
                "source snapshot"
            )
        component_id = context.component_id or "source"
        conditions = _event_conditions(node.condition, context)
        # "You and other permanents you control" never replaces damage that
        # was already headed for the redirection source itself.
        conditions["target"] = {"not_in": [context.source_ref]}
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:{component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions=conditions,
            operations=(context.source_destination,),
            label=f"{context.source_ref}: redirect damage to this source",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)


class DamageReplacementRegistry(
    RuntimeComponentRegistry[
        DamageReplacementSourceContext,
        ReplacementEffect,
    ]
):
    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DamageReplacementSourceContext,
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
def default_damage_replacement_registry() -> DamageReplacementRegistry:
    registry = DamageReplacementRegistry(
        (
            DamageQuantityReplacementHandler(),
            DamageQuantityReplacementV2Handler(),
            FixedDamagePreventionHandler(),
            AllDamagePreventionHandler(),
            StaticDamageRedirectionHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def collect_damage_replacement_effects(
    host: DamageReplacementHost,
    *,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
) -> tuple[ReplacementEffect, ...]:
    """Compile active source-pinned damage components once per batch."""

    candidates = (
        list(sources)
        if sources is not None
        else host._semantic_event_sources(zones={"battlefield"})
    )
    registry = default_damage_replacement_registry()
    effects: list[ReplacementEffect] = []
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..trigger_discovery import program_has_current_ability_fragments

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
            source.oracle_id,
            active_zone="battlefield",
            event="damage",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            if (
                CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                and not program_has_current_ability_fragments(
                    program,
                    host._effective_card_data(source),
                )
            ):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                source_destination = None
                if (
                    descriptor.get("handler_id")
                    == _STATIC_REDIRECTION_HANDLER_ID
                ):
                    from ..damage import DamageError, recipient_snapshot

                    try:
                        destination = recipient_snapshot(
                            host,
                            source.ref,
                            actor=source.controller,
                        )
                    except DamageError as exc:
                        raise SemanticNodeError(
                            "A static damage-redirection source is not "
                            "currently damageable"
                        ) from exc
                    source_destination = RedirectDamage(
                        target=destination.ref,
                        target_kind=destination.kind,
                        target_controller=destination.controller,
                        target_object_id=destination.object_id,
                        target_logical_object_id=(
                            destination.logical_object_id
                        ),
                        target_owner=destination.owner,
                        target_types=destination.types,
                        target_subtypes=destination.subtypes,
                    )
                attached = host.state.cards.get(
                    getattr(source, "attached_to", None) or ""
                )
                effects.extend(
                    registry.lower(
                        descriptor,
                        DamageReplacementSourceContext(
                            source_ref=source.ref,
                            source_controller=source.controller,
                            component_id=f"{program.key}:{descriptor_index}",
                            source_destination=source_destination,
                            attached_ref=(
                                attached.ref
                                if attached is not None
                                and attached.zone == "battlefield"
                                and not attached.phased_out
                                else None
                            ),
                            active_player=host.state.active_player,
                        ),
                    )
                )
    return tuple(effects)
