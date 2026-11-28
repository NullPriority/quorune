from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Protocol

from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
)


_STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_OPERATION = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class SemanticChoiceHandler(Protocol):
    operation: str
    handler_id: str
    schema_version: int
    rule_references: tuple[str, ...]
    capability_dependencies: tuple[str, ...]
    continuation_fields: tuple[str, ...]
    private_data: tuple[str, ...]
    projected_fields: tuple[str, ...]
    mutation_path: tuple[str, ...]
    replay_fixture: str
    test_modules: tuple[str, ...]

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation: ...

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion: ...


class SemanticChoiceRegistry:
    """Frozen one-operation/one-handler semantic-choice registry."""

    def __init__(self, handlers: Iterable[SemanticChoiceHandler] = ()):
        self._by_operation: dict[str, SemanticChoiceHandler] = {}
        self._by_id: dict[str, SemanticChoiceHandler] = {}
        self._frozen = False
        for handler in handlers:
            self.register(handler)

    def register(self, handler: SemanticChoiceHandler) -> None:
        if self._frozen:
            raise SemanticChoiceError("The semantic choice registry is frozen")
        operation = str(handler.operation)
        handler_id = str(handler.handler_id)
        if _OPERATION.fullmatch(operation) is None:
            raise SemanticChoiceError(
                f"Invalid semantic choice operation {operation!r}"
            )
        if _STABLE_ID.fullmatch(handler_id) is None:
            raise SemanticChoiceError(
                f"Invalid semantic choice handler id {handler_id!r}"
            )
        if type(handler.schema_version) is not int or handler.schema_version < 1:
            raise SemanticChoiceError(
                f"Handler {handler_id} has an invalid schema version"
            )
        if not callable(getattr(handler, "prepare", None)) or not callable(
            getattr(handler, "complete", None)
        ):
            raise SemanticChoiceError(
                f"Handler {handler_id} must own prepare and complete"
            )
        if not handler.rule_references or len(handler.rule_references) != len(
            set(handler.rule_references)
        ):
            raise SemanticChoiceError(
                f"Handler {handler_id} has invalid rule references"
            )
        dependencies = tuple(handler.capability_dependencies)
        if len(dependencies) != len(set(dependencies)) or any(
            _STABLE_ID.fullmatch(value) is None for value in dependencies
        ):
            raise SemanticChoiceError(
                f"Handler {handler_id} has invalid capability dependencies"
            )
        if operation in self._by_operation:
            raise SemanticChoiceError(
                f"Duplicate semantic choice operation {operation!r}"
            )
        if handler_id in self._by_id:
            raise SemanticChoiceError(
                f"Duplicate semantic choice handler id {handler_id!r}"
            )
        self._by_operation[operation] = handler
        self._by_id[handler_id] = handler

    def freeze(self) -> "SemanticChoiceRegistry":
        self._frozen = True
        return self

    def handler_for_operation(self, operation: str) -> SemanticChoiceHandler:
        try:
            return self._by_operation[str(operation).casefold()]
        except KeyError as exc:
            raise SemanticChoiceError(
                f"Unknown semantic choice operation {operation!r}"
            ) from exc

    def requires_object_rows(self, effect: Mapping[str, Any]) -> bool:
        """Use complete object facts unless the typed choice owner opts out."""

        handler = self._by_operation.get(str(effect.get("op") or "").casefold())
        requirement = getattr(handler, "requires_object_rows", None)
        return not callable(requirement) or requirement(effect)

    def decode_continuation(
        self,
        value: Mapping[str, Any],
    ) -> tuple[SemanticChoiceHandler, SemanticChoiceContinuation]:
        if value.get("schema_version") == 2:
            handler_id = value.get("handler_id")
            if not isinstance(handler_id, str):
                raise SemanticChoiceError(
                    "Semantic continuation requires a handler_id"
                )
            handler = self._by_id.get(handler_id)
            if handler is None:
                raise SemanticChoiceError(
                    f"Unknown semantic choice handler {handler_id!r}"
                )
            continuation = SemanticChoiceContinuation.from_dict(value)
        else:
            effect = value.get("effect")
            if not isinstance(effect, Mapping):
                raise SemanticChoiceError(
                    "Legacy semantic continuation requires an effect mapping"
                )
            operation = effect.get("op")
            if not isinstance(operation, str):
                raise SemanticChoiceError(
                    "Legacy semantic continuation requires an operation"
                )
            handler = self.handler_for_operation(operation)
            continuation = SemanticChoiceContinuation.from_dict(
                value,
                legacy_handler_id=handler.handler_id,
                legacy_handler_version=handler.schema_version,
            )
        if continuation.handler_id != handler.handler_id:
            raise SemanticChoiceError("Semantic choice handler identity changed")
        if continuation.handler_version != handler.schema_version:
            raise SemanticChoiceError("Semantic choice handler version changed")
        effect_operation = continuation.effect.get("op")
        if effect_operation != handler.operation:
            raise SemanticChoiceError("Semantic choice operation changed")
        return handler, continuation

    def describe(self, operation: str) -> dict[str, Any]:
        handler = self.handler_for_operation(operation)
        return {
            "operation": handler.operation,
            "handler_id": handler.handler_id,
            "schema_version": handler.schema_version,
            "rule_references": list(handler.rule_references),
            "capability_dependencies": list(handler.capability_dependencies),
            "continuation_fields": list(handler.continuation_fields),
            "private_data": list(handler.private_data),
            "projected_fields": list(handler.projected_fields),
            "mutation_path": list(handler.mutation_path),
            "replay_fixture": handler.replay_fixture,
            "test_modules": list(handler.test_modules),
        }

    def inventory(self) -> list[dict[str, Any]]:
        return [
            self.describe(operation)
            for operation in sorted(self._by_operation)
        ]

    @property
    def operations(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_operation))

    @property
    def fingerprint(self) -> str:
        serialized = json.dumps(
            {"schema_version": 1, "handlers": self.inventory()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
