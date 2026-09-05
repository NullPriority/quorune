from __future__ import annotations

"""Typed target-choice owner for represented Storm copy creation."""

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..errors import GameRuleError
from ..model import StackItem
from ..replacement.immutable import FrozenMap, thaw_value
from ..spell_copy_engine_adapter import dispatch_normalized_spell_copy_event
from ..storm import STORM_SEMANTIC_KEY, validated_storm_trigger
from .model import (
    SelectionContinuation,
    SelectionContract,
    SelectionModelError,
    decode_selection_continuation,
)


STORM_OPERATION_ID = "selection.target.storm.v1"


@dataclass(frozen=True, slots=True)
class StormCompletionContext:
    seat: str
    response: Mapping[str, Any]
    trigger: StackItem
    count: int
    template: dict[str, Any]


class StormTargetChoiceHost(Protocol):
    state: Any
    seats: list[str]
    semantics: Any
    permissions: Any


class StormTargetChoiceOwnerMixin:
    """Own Storm target projection, validation, and copy commitment."""

    def _storm_selection_continuation(
        self,
        *,
        item: StackItem,
        count: int,
        template: Mapping[str, Any],
    ) -> SelectionContinuation:
        return SelectionContinuation(
            contract=SelectionContract.TARGETING,
            operation_id=STORM_OPERATION_ID,
            actor=item.controller,
            state_revision=self.state.revision,
            stack_ref=item.ref,
            source_ref=self._stack_source_ref(item),
            visibility="public",
            payload=FrozenMap(
                {
                    "copy_count": count,
                    "copy_template": copy.deepcopy(dict(template)),
                }
            ),
        )

    def _prepare_storm_resolution(self, item: StackItem) -> None:
        count, template = validated_storm_trigger(item)
        if count == 0:
            self.state.stack.remove(item)
            self._log(
                item.controller,
                "stack.resolve",
                f"Resolved {item.ref} {item.label} with no copies.",
                {"stack": item.ref, "copy_count": 0},
                importance=2,
            )
            self._grant_priority(self.state.active_player)
            return
        target_schema = template.get("target_schema")
        if not isinstance(target_schema, Mapping):
            context = StormCompletionContext(
                seat=item.controller,
                response={"copy_targets": [[] for _ in range(count)]},
                trigger=item,
                count=count,
                template=template,
            )
            copies = self._build_storm_copies(context)
            self._commit_storm_copies(context, copies)
            return
        public_schema = (
            self._public_target_schema(
                item.controller,
                target_schema,
                source_ref=item.ref,
            )
            if isinstance(target_schema, Mapping)
            else None
        )
        copies = [
            {
                "copy_index": index,
                "default_targets": copy.deepcopy(
                    template.get("targets") or []
                ),
                "modes": copy.deepcopy(template.get("modes") or []),
                "target_schema": copy.deepcopy(public_schema),
            }
            for index in range(count)
        ]
        self.permissions.issue(
            kind="semantic.storm",
            role="pilot",
            actors=[item.controller],
            allowed_actions=["choose"],
            payload_by_actor={
                item.controller: {
                    "stack": item.ref,
                    "prompt": (
                        "Choose targets for each storm copy, or keep the "
                        "copied targets."
                    ),
                    "copies": copies,
                    "legal_actions": [
                        {
                            "id": "choose",
                            "action": "choose",
                            "choice_schema": {
                                "field": "copy_targets",
                                "copy_count": count,
                                "may_keep_default": True,
                            },
                        }
                    ],
                }
            },
            continuation={
                "selection": self._storm_selection_continuation(
                    item=item,
                    count=count,
                    template=template,
                ).to_dict()
            },
        )

    def _storm_completion_context(
        self,
        decision: Any,
    ) -> StormCompletionContext:
        seat = decision.actors[0]
        response = decision.responses[seat]
        raw_continuation = decision.continuation
        legacy: SelectionContinuation | None = None
        if "selection" not in raw_continuation:
            legacy_stack_ref = str(raw_continuation.get("stack_ref") or "")
            legacy_trigger = next(
                (
                    candidate
                    for candidate in self.state.stack
                    if candidate.ref == legacy_stack_ref
                    and candidate.semantic_key == STORM_SEMANTIC_KEY
                ),
                None,
            )
            if legacy_trigger is not None:
                legacy_count, legacy_template = validated_storm_trigger(
                    legacy_trigger
                )
                legacy = SelectionContinuation(
                    contract=SelectionContract.TARGETING,
                    operation_id=STORM_OPERATION_ID,
                    actor=seat,
                    state_revision=decision.created_revision,
                    stack_ref=legacy_stack_ref,
                    source_ref=self._stack_source_ref(legacy_trigger),
                    visibility="public",
                    payload=FrozenMap(
                        {
                            "copy_count": legacy_count,
                            "copy_template": legacy_template,
                        }
                    ),
                )
        try:
            selection = decode_selection_continuation(
                raw_continuation,
                expected_contract=SelectionContract.TARGETING,
                expected_operation_id=STORM_OPERATION_ID,
                legacy=legacy,
            )
        except SelectionModelError as exc:
            raise GameRuleError(str(exc)) from exc
        if selection.actor != seat:
            raise GameRuleError("Storm choice actor changed")
        if selection.state_revision != decision.created_revision:
            raise GameRuleError("Storm choice state revision changed")
        if selection.visibility != "public":
            raise GameRuleError("Storm choice visibility changed")
        trigger = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == selection.stack_ref
                and candidate.semantic_key == STORM_SEMANTIC_KEY
            ),
            None,
        )
        if trigger is None:
            raise GameRuleError("The storm trigger is no longer on the stack")
        if trigger.controller != seat:
            raise GameRuleError("Storm choice controller changed")
        count, template = validated_storm_trigger(trigger)
        if "selection" in raw_continuation:
            payload = thaw_value(selection.payload)
            if selection.source_ref != self._stack_source_ref(trigger):
                raise GameRuleError("Storm choice source identity changed")
            if payload.get("copy_count") != count or payload.get(
                "copy_template"
            ) != template:
                raise GameRuleError("Storm copy specification changed")
        return StormCompletionContext(
            seat=seat,
            response=response,
            trigger=trigger,
            count=count,
            template=template,
        )

    def _build_storm_copies(
        self,
        context: StormCompletionContext,
    ) -> list[StackItem]:
        seat = context.seat
        response = context.response
        trigger = context.trigger
        count = context.count
        template = context.template
        submitted = response.get("copy_targets")
        if submitted is None:
            submitted = [
                copy.deepcopy(template.get("targets") or [])
                for _ in range(count)
            ]
        if not isinstance(submitted, list) or len(submitted) != count:
            raise GameRuleError(
                "Storm target selection must contain one entry per copy"
            )
        program = self.semantics.get(template.get("semantic_key"))
        target_schema = template.get("target_schema")
        copies: list[StackItem] = []
        for raw_targets in submitted:
            normalized = self._normalize_target_submission(raw_targets)
            selected = (
                [str(value) for value in normalized]
                if all(
                    not isinstance(value, Mapping)
                    for value in normalized
                )
                else []
            )
            defaults = [
                str(value) for value in template.get("targets") or []
            ]
            if selected == defaults:
                grouped = copy.deepcopy(
                    dict(template.get("target_groups") or {})
                )
            else:
                selected, grouped = self._validate_semantic_targets(
                    seat,
                    program,
                    normalized,
                    modes=list(template.get("modes") or []),
                    source_ref=trigger.ref,
                    target_schema=(
                        target_schema
                        if isinstance(target_schema, Mapping)
                        else None
                    ),
                )
            copy_ref = self._next_ref("S")
            copies.append(
                StackItem(
                    stack_id=self._stable_runtime_id("stack", copy_ref),
                    ref=copy_ref,
                    kind="spell_copy",
                    controller=seat,
                    label=f"{template.get('label') or 'Spell'} copy",
                    semantic_key=template.get("semantic_key"),
                    targets=selected,
                    modes=list(template.get("modes") or []),
                    x_value=template.get("x_value"),
                    default_destination=template.get(
                        "default_destination"
                    ),
                    visibility=list(self.seats),
                    referred_object_ids=list(
                        template.get("referred_object_ids") or []
                    ),
                    context={
                        "target_groups": grouped,
                        "target_snapshots": {
                            ref: self._target_snapshot(ref)
                            for ref in selected
                            if ref is not None
                        },
                        "targets_revalidated": False,
                    },
                )
            )
        return copies

    def _commit_storm_copies(
        self,
        context: StormCompletionContext,
        copies: list[StackItem],
    ) -> None:
        seat = context.seat
        trigger = context.trigger
        template = context.template
        source_card = self.state.cards.get(
            str(template.get("card_object_id") or "")
        )
        source_data = (
            self._copyable_characteristics(source_card)
            if source_card is not None
            else {
                "name": str(template.get("label") or "Spell"),
                "type_line": "Instant",
            }
        )
        for copy_item in copies:
            copy_object = self._create_copy_object(
                controller=seat,
                source=source_card,
                characteristics=source_data,
                object_kind="spell_copy",
                zone="stack",
            )
            copy_item.card_object_id = copy_object.object_id
        self.state.stack.remove(trigger)
        self.state.stack.extend(copies)
        source_spell_ref = str(template.get("source_spell_ref") or "")
        semantic_trigger_refs: list[str] = []
        if source_spell_ref:
            for copy_item in copies:
                semantic_trigger_refs.extend(
                    dispatch_normalized_spell_copy_event(
                        self,
                        copy_item,
                        copied_from_stack_ref=source_spell_ref,
                    )
                )
        self._log(
            seat,
            "stack.storm.copy",
            f"{seat} created {len(copies)} storm copy/copies.",
            {
                "source_trigger": trigger.ref,
                "copies": [copy_item.ref for copy_item in copies],
                "targets": [
                    copy.deepcopy(copy_item.targets)
                    for copy_item in copies
                ],
            },
            importance=2,
        )
        if not semantic_trigger_refs:
            self._grant_priority(self.state.active_player)

    def _complete_storm_choice(self, decision: Any) -> None:
        context = self._storm_completion_context(decision)
        copies = self._build_storm_copies(context)
        self._commit_storm_copies(context, copies)


__all__ = ["StormTargetChoiceOwnerMixin"]
