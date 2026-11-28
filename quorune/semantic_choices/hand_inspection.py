from __future__ import annotations

"""Controller-scoped choices over one legally inspected target hand."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.hand_inspection import (
    DEFENDING_HAND_OBSERVATION_VERSION_FIELD,
    FIXED_HAND_INSPECTION_OPERATION,
    HandCardPredicateSpec,
    HandInspectionAction,
    HandInspectionError,
    HandInspectionMode,
    is_defending_hand_observation,
    selected_hand_reveal_discard_effect,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.intents import (
    InspectHandIntent,
    MoveObjectsSimultaneouslyIntent,
)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ObjectChoice,
    ReferenceSetChoice,
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


_SELECTED_REVEAL_FIELDS = frozenset({"_reveal_candidates", "_selected_subset", "_stack_label"})


def _hand_rows(query, target):
    return tuple(sorted(query.objects(zones=("hand",), owner=target), key=lambda row: row.ref))


def _hand_identities(rows):
    return tuple((row.ref, row.object_id, row.logical_object_id) for row in rows)


def _retained_identities(value):
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(tuple(row) if isinstance(row, (list, tuple)) else () for row in value)


def _selected_reveal_base(effect):
    actor, target = effect.get("player"), effect.get("target")
    if (
        type(actor) is not str or not actor or type(target) is not str or not target
        or set(effect) - (_BASE_FIELDS | _SELECTED_REVEAL_FIELDS)
        or thaw_value(FrozenMap({key: effect[key] for key in _BASE_FIELDS if key in effect}))
           != selected_hand_reveal_discard_effect(player=actor, target=target)
        or ("_reveal_candidates" in effect and "_selected_subset" in effect)
    ):
        raise SemanticChoiceError("Selected hand reveal has an unsupported phase")
    return actor, target


def _selected_subset_rows(effect, rows):
    selected = effect.get("_selected_subset")
    if not isinstance(selected, (list, tuple)):
        raise SemanticChoiceError("Selected reveal requires its retained original hand subset")
    identities = _retained_identities(selected)
    chosen = tuple(row for row in rows if (row.ref, row.object_id, row.logical_object_id) in identities)
    if (not chosen or len(chosen) != min(3, len(rows)) or _hand_identities(chosen) != identities):
        raise SemanticChoiceError("Selected reveal does not match the original hand subset")
    return chosen


def _subset_discard_stage(target, controller, rows):
    return FrozenMap({
        **selected_hand_reveal_discard_effect(player=controller, target=target),
        "_selected_subset": _hand_identities(rows),
    })


def _prepare_selected_reveal(effect, context):
    actor, target = _selected_reveal_base(effect)
    if actor != context.actor or target not in context.query.active_seats:
        raise SemanticChoiceError("Selected hand reveal actor or target is unavailable")
    rows = _hand_rows(context.query, target)
    if "_selected_subset" in effect:
        if actor != context.stack_controller:
            raise SemanticChoiceError("Only the resolving controller chooses a revealed card")
        chosen = _selected_subset_rows(effect, rows)
        refs = tuple(row.ref for row in chosen)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose one of the revealed cards to discard.",
                choice=ObjectChoice(field_name="card", legal_refs=refs, zones=("hand",), visibility="actor_private"),
                public_context=FrozenMap({"stack": context.stack_ref, "target": target, "objects": _private_cards(chosen)}),
            ),
            continuation_effect=FrozenMap({**dict(effect), "_stack_label": context.stack_label}),
            preparation_intents=(InspectHandIntent(
                actor=actor, player=target, refs=refs, reason=context.stack_label,
                public=True, scoped_stack_ref=context.stack_ref,
            ),),
        )
    if actor != target:
        raise SemanticChoiceError("Only the hand's player chooses cards to reveal")
    identities = _hand_identities(rows)
    if "_reveal_candidates" in effect and _retained_identities(effect["_reveal_candidates"]) != identities:
        raise SemanticChoiceError("The original reveal candidates changed")
    if len(rows) <= 3:
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(
                reason="The target has at most three cards in hand.",
                prepend_effects=(_subset_discard_stage(target, context.stack_controller, rows),) if rows else (),
            ),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose exactly three cards from your hand to reveal.",
            choice=ReferenceSetChoice(field_name="cards", legal_refs=tuple(row.ref for row in rows), minimum=3, maximum=3),
            public_context=FrozenMap({"stack": context.stack_ref, "target": target, "objects": _private_cards(rows)}),
        ),
        continuation_effect=FrozenMap({**dict(effect), "_reveal_candidates": identities}),
    )


def _complete_hand_inspection_move(*, actor, selected, action, reason, query):
    return SemanticChoiceCompletion(
        intents=(MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=(selected,), expected_zones=("hand",),
            destination="graveyard" if action is HandInspectionAction.DISCARD else "exile",
            reason=reason,
            transition_kind=ZoneTransitionKind.DISCARD if action is HandInspectionAction.DISCARD else ZoneTransitionKind.ORDINARY,
            discard_effect_origin=(getattr(query, "discard_effect_origin", None)
                                   if action is HandInspectionAction.DISCARD else None),
        ),),
    )


def _complete_selected_reveal(continuation, response, query):
    effect = continuation.effect
    actor, target = _selected_reveal_base(effect)
    rows = _hand_rows(query, target)
    controller = continuation.semantic_frame.controller
    if "_selected_subset" in effect:
        if actor != controller or type(effect.get("_stack_label")) is not str:
            raise SemanticChoiceError("The revealed-card choice belongs to the resolving controller")
        chosen = _selected_subset_rows(effect, rows)
        selected = response.get("card")
        if type(selected) is not str or not any(row.ref == selected for row in chosen):
            raise SemanticChoiceError("Only a retained revealed card can be discarded")
        return _complete_hand_inspection_move(
            actor=actor, selected=selected, action=HandInspectionAction.DISCARD,
            reason=effect["_stack_label"], query=query,
        )

    if actor != target or _retained_identities(effect.get("_reveal_candidates")) != _hand_identities(rows) or len(rows) <= 3:
        raise SemanticChoiceError("The private reveal choice lost its original candidates")
    submitted = response.get("cards")
    if (
        not isinstance(submitted, (list, tuple)) or len(submitted) != 3
        or any(type(ref) is not str for ref in submitted) or len(set(submitted)) != 3
    ):
        raise SemanticChoiceError("The hand's player must reveal exactly three original cards")
    chosen = tuple(row for row in rows if row.ref in submitted)
    if len(chosen) != 3:
        raise SemanticChoiceError("The reveal includes a card outside the original hand")
    return SemanticChoiceCompletion(prepend_effects=(_subset_discard_stage(target, controller, chosen),))


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
        sorted(_BASE_FIELDS | _CONTINUATION_FIELDS | _SELECTED_REVEAL_FIELDS | {DEFENDING_HAND_OBSERVATION_VERSION_FIELD})
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
        if DEFENDING_HAND_OBSERVATION_VERSION_FIELD in effect:
            if not is_defending_hand_observation(
                effect, actor=effect.get("player"), defender=effect.get("target"),
            ):
                raise SemanticChoiceError("Private defending hand observation is malformed")
        elif not _BASE_FIELDS.issubset(effect) or set(effect) - (
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
        if effect.get("inspection") == HandInspectionMode.REVEAL_SELECTED_THREE.value:
            return _prepare_selected_reveal(effect, context)
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
            keep_revealed=action != HandInspectionAction.OBSERVE,
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
                        discard_effect_origin=getattr(context.query, "discard_effect_origin", None),
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
        if continuation.effect.get("inspection") == HandInspectionMode.REVEAL_SELECTED_THREE.value:
            return _complete_selected_reveal(continuation, response, query)
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
        return _complete_hand_inspection_move(
            actor=str(continuation.effect["_choice_actor"]), selected=selected, action=action,
            reason=str(continuation.effect["_stack_label"]), query=query,
        )


HAND_INSPECTION_CHOICE_HANDLERS = (FixedHandInspectionHandler(),)


__all__ = [
    "FixedHandInspectionHandler",
    "HAND_INSPECTION_CHOICE_HANDLERS",
]
