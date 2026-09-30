from __future__ import annotations

"""Controller-scoped choices over one legally inspected target hand."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.hand_inspection import (
    FIXED_HAND_INSPECTION_OPERATION,
    HandCardPredicateSpec,
    HandInspectionAction,
    HandInspectionError,
    HandInspectionMode,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import (
    InspectHandIntent,
    MoveObjectsSimultaneouslyIntent,
)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ObjectChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_BASE_FIELDS = {
    "op",
    "player",
    "target",
    "inspection",
    "action",
    "predicate",
}
_CONTINUATION_FIELDS = {
    "_choice_actor",
    "_inspection_refs",
    "_legal_refs",
    "_selected_refs",
    "_stack_label",
    "_target_player",
}


def _private_cards(rows: tuple[Any, ...]) -> list[dict[str, str]]:
    return [{"id": row.ref, "name": row.printed_name} for row in rows]


def _predicate(effect: Mapping[str, Any]) -> HandCardPredicateSpec | None:
    value = effect.get("predicate")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SemanticChoiceError("Hand inspection predicate is malformed")
    try:
        return HandCardPredicateSpec.from_dict(value)
    except (HandInspectionError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedHandInspectionHandler:
    operation: str = FIXED_HAND_INSPECTION_OPERATION
    handler_id: str = "choice.hand.target-inspection.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 115.1",
        "CR 400.7",
        "CR 402.3",
        "CR 608.2b",
        "CR 608.2d",
        "CR 608.2h",
        "CR 701.9",
        "CR 701.20a",
    )
    capability_dependencies: tuple[str, ...] = (
        "target.revalidate_resolution",
        "zone.change.destination_replacement",
    )
    continuation_fields: tuple[str, ...] = tuple(
        sorted(_BASE_FIELDS | _CONTINUATION_FIELDS)
    )
    private_data: tuple[str, ...] = ("target hand", "eligible card identities")
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "InspectHandIntent",
        "MoveObjectsSimultaneouslyIntent",
        "ZoneTransitionOwner.move_cards_simultaneously",
    )
    replay_fixture: str = "fixed-target-hand-inspection"
    test_modules: tuple[str, ...] = ("tests.test_fixed_hand_inspection",)

    @staticmethod
    def _shape(
        effect: Mapping[str, Any],
    ) -> tuple[
        HandInspectionMode,
        HandInspectionAction,
        HandCardPredicateSpec | None,
    ]:
        if not _BASE_FIELDS.issubset(effect) or set(effect) - (
            _BASE_FIELDS | _CONTINUATION_FIELDS
        ):
            raise SemanticChoiceError(
                "Hand inspection effect fields are incomplete or unknown"
            )
        if (
            effect.get("op") != FIXED_HAND_INSPECTION_OPERATION
            or type(effect.get("player")) is not str
            or not effect.get("player")
            or type(effect.get("target")) is not str
            or not effect.get("target")
        ):
            raise SemanticChoiceError("Hand inspection effect is malformed")
        try:
            mode = HandInspectionMode(str(effect.get("inspection") or ""))
            action = HandInspectionAction(str(effect.get("action") or ""))
        except ValueError as exc:
            raise SemanticChoiceError(
                "Hand inspection mode or action is unsupported"
            ) from exc
        predicate = _predicate(effect)
        if (action == HandInspectionAction.OBSERVE) != (predicate is None):
            raise SemanticChoiceError(
                "Hand inspection predicate does not match its action"
            )
        if (
            action == HandInspectionAction.DISCARD_ALL
            and mode != HandInspectionMode.REVEAL
        ):
            raise SemanticChoiceError(
                "Bulk hand discard requires a public reveal"
            )
        return mode, action, predicate

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        mode, action, predicate = self._shape(effect)
        target = str(effect.get("_target_player") or effect["target"])
        if effect.get("player") not in {"$controller", context.actor}:
            raise SemanticChoiceError(
                "Hand inspection actor does not control the effect"
            )
        if target not in context.query.active_seats:
            raise SemanticChoiceError("Hand inspection target is unavailable")
        rows = tuple(
            sorted(
                context.query.objects(zones=("hand",), owner=target),
                key=lambda row: row.ref,
            )
        )
        inspection_refs = tuple(
            str(value)
            for value in effect.get(
                "_inspection_refs", tuple(row.ref for row in rows)
            )
        )
        if len(inspection_refs) != len(rows) or set(inspection_refs) != {
            row.ref for row in rows
        }:
            raise SemanticChoiceError("The inspected hand changed")
        legal_rows = (
            tuple(
                row
                for row in rows
                if predicate is not None and predicate.matches(row)
            )
            if predicate is not None
            else ()
        )
        legal_refs = tuple(
            str(value)
            for value in effect.get(
                "_legal_refs", tuple(row.ref for row in legal_rows)
            )
        )
        if set(legal_refs) != {row.ref for row in legal_rows}:
            raise SemanticChoiceError(
                "Hand inspection legal options changed"
            )
        continuation = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_inspection_refs": inspection_refs,
                "_legal_refs": legal_refs,
                "_stack_label": context.stack_label,
                "_target_player": target,
            }
        )
        inspect = InspectHandIntent(
            actor=context.actor,
            player=target,
            refs=inspection_refs,
            reason=context.stack_label,
            public=mode == HandInspectionMode.REVEAL,
        )
        if action == HandInspectionAction.OBSERVE or not legal_refs:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=continuation,
                preparation_intents=(inspect,),
                auto_continue=AutoContinue(
                    reason=(
                        "target hand inspected"
                        if action == HandInspectionAction.OBSERVE
                        else "no matching target-hand card"
                    )
                ),
            )
        if action == HandInspectionAction.DISCARD_ALL:
            frozen = tuple(
                str(value)
                for value in effect.get("_selected_refs", legal_refs)
            )
            if set(frozen) != set(legal_refs):
                raise SemanticChoiceError(
                    "Bulk hand-discard membership changed"
                )
            continuation = FrozenMap(
                {**dict(continuation), "_selected_refs": frozen}
            )
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=continuation,
                preparation_intents=(
                    inspect,
                    MoveObjectsSimultaneouslyIntent(
                        actor=context.actor,
                        object_refs=frozen,
                        expected_zones=("hand",),
                        destination="graveyard",
                        reason=context.stack_label,
                        transition_kind=ZoneTransitionKind.DISCARD,
                    ),
                ),
                auto_continue=AutoContinue(
                    reason="matching hand cards discarded"
                ),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Choose one matching card from the inspected hand to "
                    f"{action.value}."
                ),
                choice=ObjectChoice(
                    field_name="card",
                    legal_refs=legal_refs,
                    zones=("hand",),
                    visibility="actor_private",
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "target": target,
                        "objects": _private_cards(rows),
                    }
                ),
            ),
            continuation_effect=continuation,
            preparation_intents=(inspect,),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        _mode, action, predicate = self._shape(continuation.effect)
        if action not in {
            HandInspectionAction.DISCARD,
            HandInspectionAction.EXILE,
        } or predicate is None:
            raise SemanticChoiceError(
                "Hand inspection continuation does not require a choice"
            )
        selected = str(response.get("card") or "")
        legal = {
            str(value) for value in continuation.effect.get("_legal_refs", ())
        }
        target = str(continuation.effect.get("_target_player") or "")
        row = query.object(selected, zones=("hand",))
        if (
            selected not in legal
            or row is None
            or row.owner != target
            or not predicate.matches(row)
        ):
            raise SemanticChoiceError(
                "Selected target-hand card is no longer legal"
            )
        return SemanticChoiceCompletion(
            intents=(
                MoveObjectsSimultaneouslyIntent(
                    actor=str(continuation.effect["_choice_actor"]),
                    object_refs=(selected,),
                    expected_zones=("hand",),
                    destination=(
                        "graveyard"
                        if action == HandInspectionAction.DISCARD
                        else "exile"
                    ),
                    reason=str(continuation.effect["_stack_label"]),
                    transition_kind=(
                        ZoneTransitionKind.DISCARD
                        if action == HandInspectionAction.DISCARD
                        else ZoneTransitionKind.ORDINARY
                    ),
                ),
            )
        )


HAND_INSPECTION_CHOICE_HANDLERS = (FixedHandInspectionHandler(),)


__all__ = [
    "FixedHandInspectionHandler",
    "HAND_INSPECTION_CHOICE_HANDLERS",
]
