from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from ..replacement import CreateAdditionalToken, MultiplyTokenCreation
from ..replacement_effects import (
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
from ..standard_token_abilities import (
    TOKEN_ABILITY_PROFILE_FIELD,
    standard_token_characteristics,
)


_ADDITIONAL_TOKEN_HANDLER_ID = "replacement.token.additional.v1"
_GENERIC_ADDITIONAL_TOKEN_HANDLER_ID = "replacement.token.additional.v2"
_TOKEN_QUANTITY_HANDLER_ID = "replacement.token.quantity.v1"


@dataclass(frozen=True, slots=True)
class TokenDefinition:
    name: str
    type_line: str
    colors: tuple[str, ...] = ()
    power: str | None = None
    toughness: str | None = None
    keywords: tuple[str, ...] = ()
    display_text: str = ""
    ability_profile: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TokenDefinition":
        allowed = {
            "name",
            "type_line",
            "colors",
            "power",
            "toughness",
            "keywords",
            "display_text",
            "ability_profile",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise SemanticNodeError(
                "additional token has unknown fields: " + ", ".join(unknown)
            )
        name = str(value.get("name") or "").strip()
        type_line = str(value.get("type_line") or "").strip()
        if not name or not type_line:
            raise SemanticNodeError(
                "additional token requires nonempty name and type_line"
            )
        colors = nonempty_strings(
            value.get("colors", []), field="token.colors"
        )
        keywords = nonempty_strings(
            value.get("keywords", []), field="token.keywords"
        )
        power = value.get("power")
        toughness = value.get("toughness")
        display_text = value.get("display_text", "")
        if type(display_text) is not str:
            raise SemanticNodeError(
                "additional token display_text must be a string"
            )
        ability_profile = value.get("ability_profile")
        if ability_profile is not None and (
            type(ability_profile) is not str or not ability_profile
        ):
            raise SemanticNodeError(
                "additional token ability_profile must be null or nonempty"
            )
        if (power is None) != (toughness is None):
            raise SemanticNodeError(
                "additional token power and toughness must appear together"
            )
        return cls(
            name=name,
            type_line=type_line,
            colors=colors,
            power=None if power is None else str(power),
            toughness=None if toughness is None else str(toughness),
            keywords=keywords,
            display_text=display_text,
            ability_profile=ability_profile,
        )

    def characteristics(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type_line": self.type_line,
            "colors": list(self.colors),
            "keywords": list(self.keywords),
        }
        if self.power is not None:
            value["power"] = self.power
            value["toughness"] = self.toughness
        if self.display_text:
            value["display_text"] = self.display_text
        if self.ability_profile is not None:
            value[TOKEN_ABILITY_PROFILE_FIELD] = self.ability_profile
        try:
            return standard_token_characteristics(value)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class AdditionalTokenReplacementNode:
    created_types_all: tuple[str, ...]
    created_subtypes_all: tuple[str, ...]
    event_controller: str
    quantity: int
    token: TokenDefinition


@dataclass(frozen=True, slots=True)
class TokenCreationReplacementContext:
    source_ref: str
    source_controller: str
    event_controller: str
    created_types: tuple[str, ...]
    created_subtypes: tuple[str, ...] = ()
    component_id: str = ""

    def __post_init__(self) -> None:
        if not self.source_ref:
            raise SemanticNodeError("A token replacement source ref is required")
        if not self.source_controller or not self.event_controller:
            raise SemanticNodeError(
                "Token replacement controllers must be nonempty"
            )
        for field_name in ("created_types", "created_subtypes"):
            supplied = getattr(self, field_name)
            if not isinstance(supplied, (list, tuple)) or any(
                type(value) is not str or not value.strip()
                for value in supplied
            ):
                raise SemanticNodeError(
                    f"Token replacement context {field_name} must be "
                    "nonempty strings"
                )
            normalized = tuple(
                sorted(value.casefold().strip() for value in supplied)
            )
            if len(normalized) != len(set(normalized)):
                raise SemanticNodeError(
                    f"Token replacement context {field_name} must be unique"
                )
            object.__setattr__(self, field_name, normalized)


def _token_characteristic_parts(
    type_line: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    left, separator, right = type_line.partition("—")
    card_types = tuple(
        sorted(
            {
                value
                for value in left.strip().casefold().split()
                if value and value != "token"
            }
        )
    )
    subtypes = tuple(
        sorted(set(right.strip().casefold().split()))
    ) if separator else ()
    return card_types, subtypes


@dataclass(frozen=True, slots=True)
class AdditionalTokenIntent:
    handler_id: str
    source_ref: str
    quantity: int
    token: TokenDefinition


@dataclass(frozen=True, slots=True)
class TokenCreationReplacementResolution:
    batch: ReplacementEventBatch
    event: ReplaceableEvent
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    pending: ReplacementBatchChoice | None
    consumed_selections: int

    @property
    def tokens(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            token
            for token in self.event.payload.get("tokens", ())
            if isinstance(token, Mapping)
        )


def resolve_token_creation_replacements(
    *,
    event_id: str,
    controller: str,
    tokens: Sequence[Mapping[str, Any]],
    created_types: Sequence[str],
    created_subtypes: Sequence[str],
    effects: Sequence[ReplacementEffect],
    apnap_order: Sequence[str],
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    require_all_selections: bool = True,
) -> TokenCreationReplacementResolution:
    event = ReplaceableEvent(
        event_id=event_id,
        kind="token.create",
        affected_player=controller,
        payload={
            "event_controller": controller,
            "created_types": sorted(set(created_types)),
            "created_subtypes": sorted(set(created_subtypes)),
            "tokens": [dict(token) for token in tokens],
        },
    )
    progress = advance_replacement_batch(
        ReplacementEventBatch(
            batch_id=f"replacement:{event_id}",
            events=(event,),
            apnap_order=tuple(apnap_order),
        ),
        tuple(effects),
        selections=tuple(selections),
        require_all_selections=require_all_selections,
    )
    resolved_event = progress.batch.events[0]
    return TokenCreationReplacementResolution(
        batch=progress.batch,
        event=resolved_event,
        effects=tuple(effects),
        journal=progress.batch.journal,
        pending=progress.pending,
        consumed_selections=progress.consumed_selections,
    )


class TokenCreationReplacementHandler(Protocol):
    handler_id: str
    schema_version: int
    family: str
    event: str
    rule_references: tuple[str, ...]
    capability_dependencies: tuple[str, ...]

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> AdditionalTokenReplacementNode: ...

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> tuple[AdditionalTokenIntent, ...]: ...

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> ReplacementEffect: ...


@dataclass(frozen=True, slots=True)
class AdditionalTokenReplacementHandler:
    handler_id: str = _ADDITIONAL_TOKEN_HANDLER_ID
    schema_version: int = 2
    family: str = "replacement.fixed_additional_token"
    event: str = "token.create"
    rule_references: tuple[str, ...] = (
        "111.2",
        "614.1",
        "614.1a",
        "614.4",
        "614.5",
        "614.6",
        "614.16",
    )
    capability_dependencies: tuple[str, ...] = (
        "token.creation.additional_replacement",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> AdditionalTokenReplacementNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "quantity",
                "token",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Runtime handler ID does not match registry")
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
            raise SemanticNodeError("runtime handler condition must be an object")
        exact_fields(
            condition,
            {"event_controller", "created_types_all"},
            field="runtime handler condition",
        )
        event_controller = str(condition["event_controller"])
        if event_controller != "source_controller":
            raise SemanticNodeError(
                "additional token replacement currently requires "
                "event_controller=source_controller"
            )
        created_types = tuple(
            value.casefold()
            for value in nonempty_strings(
                condition["created_types_all"],
                field="condition.created_types_all",
            )
        )
        if not created_types:
            raise SemanticNodeError(
                "additional token replacement requires a created token type"
            )
        quantity = descriptor["quantity"]
        if type(quantity) is not int or quantity < 1:
            raise SemanticNodeError(
                "additional token replacement quantity must be positive"
            )
        token = descriptor["token"]
        if not isinstance(token, Mapping):
            raise SemanticNodeError("runtime handler token must be an object")
        return AdditionalTokenReplacementNode(
            created_types_all=created_types,
            created_subtypes_all=(),
            event_controller=event_controller,
            quantity=quantity,
            token=TokenDefinition.from_mapping(token),
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> tuple[AdditionalTokenIntent, ...]:
        node = self.validate(descriptor)
        if context.event_controller != context.source_controller:
            return ()
        event_types = {value.casefold() for value in context.created_types}
        if not set(node.created_types_all).issubset(event_types):
            return ()
        return (
            AdditionalTokenIntent(
                handler_id=self.handler_id,
                source_ref=context.source_ref,
                quantity=node.quantity,
                token=node.token,
            ),
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> ReplacementEffect:
        """Compile one source descriptor into the shared CR 614/616 event form."""

        node = self.validate(descriptor)
        component_id = context.component_id or (
            f"{node.token.name.casefold()}:{node.quantity}"
        )
        token_types, token_subtypes = _token_characteristic_parts(
            node.token.type_line
        )
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:{component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "event_controller": {"eq": context.source_controller},
                "created_types": {
                    "contains_all": list(node.created_types_all)
                },
            },
            operations=(CreateAdditionalToken(
                name=node.token.name,
                quantity=node.quantity,
                characteristics=node.token.characteristics(),
                card_types=token_types,
                subtypes=token_subtypes,
                handler_id=self.handler_id,
                source_ref=context.source_ref,
            ),),
            label=(
                f"{context.source_ref}: create {node.quantity} additional "
                f"{node.token.name} token"
                + ("s" if node.quantity != 1 else "")
            ),
        )


@dataclass(frozen=True, slots=True)
class GenericAdditionalTokenReplacementHandler:
    """Closed compiler-facing fixed additional-token replacement family."""

    handler_id: str = _GENERIC_ADDITIONAL_TOKEN_HANDLER_ID
    schema_version: int = 2
    family: str = "replacement.fixed_additional_token"
    event: str = "token.create"
    rule_references: tuple[str, ...] = (
        "111.2",
        "614.1",
        "614.1a",
        "614.4",
        "614.5",
        "614.6",
        "614.16",
    )
    capability_dependencies: tuple[str, ...] = (
        "token.creation.additional_replacement",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> AdditionalTokenReplacementNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "quantity",
                "token",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Runtime handler ID does not match registry")
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
            raise SemanticNodeError("runtime handler condition must be an object")
        exact_fields(
            condition,
            {
                "event_controller",
                "created_types_all",
                "created_subtypes_all",
            },
            field="runtime handler condition",
        )
        event_controller = str(condition["event_controller"])
        if event_controller != "source_controller":
            raise SemanticNodeError(
                "additional token replacement requires "
                "event_controller=source_controller"
            )
        created_types = tuple(
            sorted(
                value.casefold()
                for value in nonempty_strings(
                    condition["created_types_all"],
                    field="condition.created_types_all",
                )
            )
        )
        created_subtypes = tuple(
            sorted(
                value.casefold()
                for value in nonempty_strings(
                    condition["created_subtypes_all"],
                    field="condition.created_subtypes_all",
                )
            )
        )
        if len(created_types) != len(set(created_types)):
            raise SemanticNodeError(
                "condition.created_types_all values must be unique"
            )
        if len(created_subtypes) != len(set(created_subtypes)):
            raise SemanticNodeError(
                "condition.created_subtypes_all values must be unique"
            )
        quantity = descriptor["quantity"]
        if type(quantity) is not int or quantity < 1:
            raise SemanticNodeError(
                "additional token replacement quantity must be positive"
            )
        token = descriptor["token"]
        if not isinstance(token, Mapping):
            raise SemanticNodeError("runtime handler token must be an object")
        return AdditionalTokenReplacementNode(
            created_types_all=created_types,
            created_subtypes_all=created_subtypes,
            event_controller=event_controller,
            quantity=quantity,
            token=TokenDefinition.from_mapping(token),
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> tuple[AdditionalTokenIntent, ...]:
        node = self.validate(descriptor)
        if context.event_controller != context.source_controller:
            return ()
        event_types = {value.casefold() for value in context.created_types}
        event_subtypes = {
            value.casefold() for value in context.created_subtypes
        }
        if not set(node.created_types_all).issubset(event_types):
            return ()
        if not set(node.created_subtypes_all).issubset(event_subtypes):
            return ()
        return (
            AdditionalTokenIntent(
                handler_id=self.handler_id,
                source_ref=context.source_ref,
                quantity=node.quantity,
                token=node.token,
            ),
        )

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        component_id = context.component_id or (
            f"{node.token.name.casefold()}:{node.quantity}"
        )
        token_types, token_subtypes = _token_characteristic_parts(
            node.token.type_line
        )
        conditions: dict[str, Any] = {
            "event_controller": {"eq": context.source_controller},
        }
        if node.created_types_all:
            conditions["created_types"] = {
                "contains_all": list(node.created_types_all)
            }
        if node.created_subtypes_all:
            conditions["created_subtypes"] = {
                "contains_all": list(node.created_subtypes_all)
            }
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:{component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions=conditions,
            operations=(CreateAdditionalToken(
                name=node.token.name,
                quantity=node.quantity,
                characteristics=node.token.characteristics(),
                card_types=token_types,
                subtypes=token_subtypes,
                handler_id=self.handler_id,
                source_ref=context.source_ref,
            ),),
            label=(
                f"{context.source_ref}: create {node.quantity} additional "
                f"{node.token.name} token"
                + ("s" if node.quantity != 1 else "")
            ),
        )


@dataclass(frozen=True, slots=True)
class TokenQuantityReplacementHandler:
    """Closed mandatory controller-scoped token quantity multiplier."""

    handler_id: str = _TOKEN_QUANTITY_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.token_quantity"
    event: str = "token.create"
    rule_references: tuple[str, ...] = (
        "111.2",
        "614.1",
        "614.1a",
        "614.4",
        "614.5",
        "614.6",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "token.creation.quantity_replacement",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> int:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "multiplier",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Runtime handler ID does not match registry")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(f"{self.handler_id} must handle {self.event}")
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError("runtime handler condition must be an object")
        exact_fields(
            condition,
            {"event_controller"},
            field="runtime handler condition",
        )
        if condition["event_controller"] != "source_controller":
            raise SemanticNodeError(
                "token quantity replacement requires "
                "event_controller=source_controller"
            )
        multiplier = descriptor["multiplier"]
        if type(multiplier) is not int or multiplier != 2:
            raise SemanticNodeError(
                "token quantity replacement requires multiplier 2"
            )
        return multiplier

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> tuple[AdditionalTokenIntent, ...]:
        self.validate(descriptor)
        return ()

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
    ) -> ReplacementEffect:
        multiplier = self.validate(descriptor)
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:"
                f"{context.component_id or 'quantity'}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "event_controller": {"eq": context.source_controller},
            },
            operations=(
                MultiplyTokenCreation(
                    factor=multiplier,
                    handler_id=self.handler_id,
                    source_ref=context.source_ref,
                ),
            ),
            label=f"{context.source_ref}: create twice that many tokens",
        )


class TokenCreationReplacementRegistry(
    RuntimeComponentRegistry[
        TokenCreationReplacementContext,
        AdditionalTokenIntent,
    ]
):
    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: TokenCreationReplacementContext,
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
def default_token_creation_replacement_registry(
) -> TokenCreationReplacementRegistry:
    registry = TokenCreationReplacementRegistry(
        (
            AdditionalTokenReplacementHandler(),
            GenericAdditionalTokenReplacementHandler(),
            TokenQuantityReplacementHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()
