from __future__ import annotations

from .target_source import stack_targeting_source

from typing import Any, Mapping, Protocol


class TriggerTargetingHost(Protocol):
    """Narrow engine surface needed to place triggered-ability targets."""

    state: Any
    semantics: Any
    permissions: Any

    def _stack_target_schema(
        self,
        item: Any,
        program: Any,
    ) -> Mapping[str, Any] | None: ...

    def _public_target_schema(
        self,
        controller: str,
        schema: Mapping[str, Any],
        *,
        source_ref: str,
        acting_source: Any = None,
    ) -> Mapping[str, Any] | None: ...

    def _stack_source_ref(self, item: Any) -> str: ...

    def _target_selection_continuation(
        self,
        *,
        actor: str,
        item: Any,
        public_schema: Mapping[str, Any],
        trigger_creation: bool = False,
    ) -> Any: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


def begin_pending_trigger_target_selection(
    host: TriggerTargetingHost,
    *,
    decision_role: str,
    log_reason_field: str,
) -> bool:
    """Choose CR 603.3d targets as a represented trigger enters the stack.

    Dynamic typed triggers and registry-backed semantic triggers share this
    boundary.  The caller supplies only target/schema services; this module
    owns removal when mandatory targets have disappeared and issuance of the
    seat-scoped target capability.
    """

    while True:
        removed_invalid_trigger = False
        for item in host.state.stack:
            if not item.context.get("trigger_target_selection_pending"):
                continue
            program = host.semantics.get(item.semantic_key)
            target_schema = host._stack_target_schema(item, program)
            if not target_schema:
                item.context.pop("trigger_target_selection_pending", None)
                continue
            public_schema = host._public_target_schema(
                item.controller,
                target_schema,
                acting_source=stack_targeting_source(host,item), source_ref=host._stack_source_ref(item),
            )
            if public_schema is None:
                host.state.stack.remove(item)
                host._log(
                    item.controller,
                    "stack.trigger.removed",
                    (
                        f"Removed {item.ref}: {item.label}; its mandatory "
                        "targets could not be chosen."
                    ),
                    {
                        "stack": item.ref,
                        log_reason_field: "no_legal_targets",
                    },
                    importance=2,
                )
                removed_invalid_trigger = True
                break
            host.permissions.issue(
                kind="semantic.target",
                role=decision_role,
                actors=[item.controller],
                allowed_actions=["choose"],
                payload_by_actor={
                    item.controller: {
                        "stack": item.ref,
                        "prompt": f"Choose legal targets for {item.label}.",
                        "target_schema": public_schema,
                        "legal_actions": [
                            {
                                "id": "choose",
                                "action": "choose",
                                "target_schema": public_schema,
                            }
                        ],
                    }
                },
                continuation={
                    "selection": host._target_selection_continuation(
                        actor=item.controller,
                        item=item,
                        public_schema=public_schema,
                        trigger_creation=True,
                    ).to_dict()
                },
            )
            return True
        if not removed_invalid_trigger:
            return False


__all__ = ["begin_pending_trigger_target_selection"]
