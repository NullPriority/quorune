from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query
from ..replacement.immutable import FrozenMap
from ..rules.library_partition import LibraryPartitionError, partition_refs
from ..rules.library_selection import (
    LibrarySelectionArrangement,
    LibrarySelectionError,
    LibrarySelectionObjectIdentity,
)
from ..semantic_runtime.intents import (
    LibrarySelectionIntent,
    RevealLibraryCardsIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    LibraryPartitionChoice,
    ObjectChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_EFFECT_FIELDS = {
    "op",
    "player",
    "look_count",
    "public_reveal",
    "selected_reveal",
    "selection_policy",
    "minimum",
    "maximum",
    "predicate_groups",
    "remainder_destination",
    "remainder_order",
}


def _specs(value: Any) -> tuple[tuple[ObjectQuerySpec, ...], ...]:
    if not isinstance(value, (list, tuple)):
        raise SemanticChoiceError(
            "Library selection predicate groups must be an array"
        )
    groups: list[tuple[ObjectQuerySpec, ...]] = []
    try:
        for raw_group in value:
            if not isinstance(raw_group, (list, tuple)) or not raw_group:
                raise SemanticChoiceError(
                    "Library selection predicate groups must be nonempty"
                )
            groups.append(
                tuple(ObjectQuerySpec.from_dict(raw) for raw in raw_group)
            )
    except (ObjectQueryError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(
            "Library selection predicates are malformed"
        ) from exc
    return tuple(groups)


def _eligible_groups(
    rows: Sequence[Any],
    groups: tuple[tuple[ObjectQuerySpec, ...], ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(
            row.ref
            for row in rows
            if any(object_matches_query(row, spec) for spec in group)
        )
        for group in groups
    )


def _slot_assignment_exists(
    selected: tuple[str, ...],
    eligible_groups: tuple[tuple[str, ...], ...],
) -> bool:
    def assign(index: int, remaining: tuple[int, ...]) -> bool:
        if index == len(selected):
            return True
        ref = selected[index]
        return any(
            ref in eligible_groups[group]
            and assign(
                index + 1,
                tuple(value for value in remaining if value != group),
            )
            for group in remaining
        )

    return assign(0, tuple(range(len(eligible_groups))))


def _identity(row: Any) -> LibrarySelectionObjectIdentity:
    try:
        return LibrarySelectionObjectIdentity(
            object_id=row.object_id,
            logical_object_id=row.logical_object_id,
            ref=row.ref,
        )
    except LibrarySelectionError as exc:
        raise SemanticChoiceError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedLibrarySelectionChoiceHandler:
    operation: str = "fixed_library_selection"
    handler_id: str = "choice.library.fixed-selection.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 400.2",
        "CR 401.4",
        "CR 404.3",
        "CR 701.16",
    )
    capability_dependencies: tuple[str, ...] = (
        "library.select.fixed_controller",
    )
    continuation_fields: tuple[str, ...] = (
        "look_count",
        "maximum",
        "minimum",
        "op",
        "player",
        "predicate_groups",
        "public_reveal",
        "remainder_destination",
        "remainder_order",
        "selected_reveal",
        "selection_policy",
        "_choice_actor",
        "_effective_maximum",
        "_effective_minimum",
        "_eligible_groups",
        "_looked_objects",
        "_stack_label",
    )
    private_data: tuple[str, ...] = (
        "actor library top",
        "looked-at object identities",
        "eligible characteristic groups",
    )
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "RevealLibraryCardsIntent",
        "LibrarySelectionIntent",
        "ZoneTransitionOwner.move_cards_simultaneously",
        "commit_ordered_library_partition",
    )
    replay_fixture: str = "semantic-choice-fixed-library-selection"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_library_selection",
    )

    def _validated_effect(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> tuple[tuple[ObjectQuerySpec, ...], ...]:
        if set(effect) != _EFFECT_FIELDS:
            raise SemanticChoiceError(
                "Fixed library selection effects have a closed field set"
            )
        if (
            effect.get("op") != self.operation
            or effect.get("player") != context.actor
            or context.stack_controller != context.actor
            or type(effect.get("look_count")) is not int
            or effect["look_count"] <= 0
            or type(effect.get("minimum")) is not int
            or type(effect.get("maximum")) is not int
            or effect["minimum"] < 0
            or effect["maximum"] <= 0
            or effect["minimum"] > effect["maximum"]
            or effect["maximum"] > effect["look_count"]
            or type(effect.get("public_reveal")) is not bool
            or type(effect.get("selected_reveal")) is not bool
            or effect.get("selection_policy")
            not in {
                "fixed_any",
                "up_to_matching",
                "all_matching",
                "optional_slots",
            }
            or effect.get("remainder_destination")
            not in {"graveyard", "library_bottom"}
            or effect.get("remainder_order") not in {"chosen", "random"}
        ):
            raise SemanticChoiceError(
                "Fixed library selection effect is malformed"
            )
        groups = _specs(effect["predicate_groups"])
        policy = effect["selection_policy"]
        if (
            (policy == "fixed_any" and groups)
            or (
                policy in {"up_to_matching", "all_matching"}
                and len(groups) != 1
            )
            or (
                policy == "optional_slots"
                and len(groups) != effect["maximum"]
            )
        ):
            raise SemanticChoiceError(
                "Fixed library selection policy and predicates disagree"
            )
        return groups

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        groups = self._validated_effect(effect, context)
        refs = context.query.library_refs(context.actor, top_first=True)[
            : int(effect["look_count"])
        ]
        rows = []
        for ref in refs:
            row = context.query.object(ref, zones=("library",))
            if row is None:
                raise SemanticChoiceError(
                    "A looked-at library card is absent from the actor query"
                )
            rows.append(row)
        identities = tuple(_identity(row) for row in rows)
        eligible = _eligible_groups(rows, groups)
        policy = str(effect["selection_policy"])
        if policy == "fixed_any":
            effective_maximum = min(int(effect["maximum"]), len(refs))
            effective_minimum = effective_maximum
            legal_refs = refs
        else:
            legal_refs = tuple(
                dict.fromkeys(ref for group in eligible for ref in group)
            )
            effective_maximum = min(
                int(effect["maximum"]),
                len(legal_refs),
            )
            effective_minimum = (
                1
                if int(effect["minimum"]) > 0 and effective_maximum > 0
                else 0
            )
        if policy == "all_matching":
            effective_minimum = len(legal_refs)
            effective_maximum = len(legal_refs)

        continuation = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_effective_minimum": effective_minimum,
                "_effective_maximum": effective_maximum,
                "_eligible_groups": [list(group) for group in eligible],
                "_looked_objects": [
                    identity.to_dict() for identity in identities
                ],
                "_stack_label": context.stack_label,
            }
        )
        preparation_intents = (
            RevealLibraryCardsIntent(
                actor=context.actor,
                player=context.actor,
                viewer=context.actor,
                refs_top_first=refs,
                reason=context.stack_label,
                public=bool(effect["public_reveal"]),
            ),
        ) if refs else ()

        deterministic = policy == "all_matching" or not refs
        needs_remainder_order = (
            effect["remainder_order"] == "chosen"
            and len(refs) - effective_minimum > 1
        )
        if deterministic and not needs_remainder_order:
            selected = legal_refs if policy == "all_matching" else ()
            arrangement = LibrarySelectionArrangement(
                looked=identities,
                selected_refs=selected,
                remainder_refs=tuple(
                    ref for ref in refs if ref not in set(selected)
                ),
                remainder_destination=str(effect["remainder_destination"]),
                remainder_order=str(effect["remainder_order"]),
            )
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=continuation,
                auto_continue=AutoContinue(
                    reason="the fixed library partition needs no decision"
                ),
                preparation_intents=(
                    *preparation_intents,
                    self._intent(
                        continuation,
                        arrangement,
                        stack_ref=context.stack_ref,
                    ),
                ),
            )

        return SemanticChoicePreparation(
            request=self._choice_request(
                effect=effect,
                context=context,
                refs=refs,
                rows=rows,
                legal_refs=legal_refs,
                effective_minimum=effective_minimum,
                effective_maximum=effective_maximum,
            ),
            continuation_effect=continuation,
            preparation_intents=preparation_intents,
        )

    def _choice_request(
        self,
        *,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
        refs: Sequence[str],
        rows: Sequence[Any],
        legal_refs: Sequence[str],
        effective_minimum: int,
        effective_maximum: int,
    ) -> SemanticChoiceRequest:
        public_context = FrozenMap(
            {
                "stack": context.stack_ref,
                "operation": self.operation,
                "objects": [
                    {"id": row.ref, "name": row.printed_name}
                    for row in rows
                ],
            }
        )
        if effect["remainder_order"] == "chosen":
            destination = (
                "graveyard"
                if effect["remainder_destination"] == "graveyard"
                else "bottom"
            )
            choice: Any = LibraryPartitionChoice(
                field_name="cards",
                legal_refs=refs,
                visibility="actor_private",
                partitions=FrozenMap(
                    {
                        "hand": {
                            "order": "selection_order",
                            "label": "Hand",
                        },
                        destination: {
                            "order": (
                                "graveyard_top_to_bottom"
                                if destination == "graveyard"
                                else "bottom_to_top"
                            ),
                            "label": (
                                "Graveyard"
                                if destination == "graveyard"
                                else "Bottom of library"
                            ),
                        },
                    }
                ),
                legacy_destination=None,
            )
        else:
            choice = ObjectChoice(
                field_name="cards",
                legal_refs=legal_refs,
                zones=("library",),
                minimum=effective_minimum,
                maximum=effective_maximum,
                optional=effective_minimum == 0,
                visibility="actor_private",
                owner_relation="actor",
            )
        return SemanticChoiceRequest(
            prompt=(
                "Choose the looked-at cards to put into your hand and "
                "complete the remainder ordering."
            ),
            choice=choice,
            public_context=public_context,
        )

    @staticmethod
    def _intent(
        continuation_effect: Mapping[str, Any],
        arrangement: LibrarySelectionArrangement,
        *,
        stack_ref: str,
    ) -> LibrarySelectionIntent:
        return LibrarySelectionIntent(
            actor=str(continuation_effect["_choice_actor"]),
            player=str(continuation_effect["_choice_actor"]),
            arrangement=arrangement,
            reason=str(continuation_effect["_stack_label"]),
            source_stack_ref=stack_ref,
            looked_are_public=bool(
                continuation_effect["public_reveal"]
            ),
            selected_are_public=bool(
                continuation_effect["selected_reveal"]
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        del query
        effect = continuation.effect
        try:
            looked = tuple(
                LibrarySelectionObjectIdentity.from_dict(value)
                for value in effect.get("_looked_objects", ())
            )
        except (LibrarySelectionError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(
                "Library selection continuation identities are malformed"
            ) from exc
        looked_refs = tuple(value.ref for value in looked)
        raw_cards = response.get("cards")
        if isinstance(raw_cards, Mapping):
            destination = (
                "graveyard"
                if effect["remainder_destination"] == "graveyard"
                else "bottom"
            )
            if set(raw_cards) != {"hand", destination}:
                raise SemanticChoiceError(
                    "Library partition destinations are malformed"
                )
            try:
                selected = partition_refs(
                    raw_cards["hand"],
                    field="cards.hand",
                )
                remainder = partition_refs(
                    raw_cards[destination],
                    field=f"cards.{destination}",
                )
            except LibraryPartitionError as exc:
                raise SemanticChoiceError(str(exc)) from exc
        else:
            try:
                selected = partition_refs(raw_cards, field="cards")
            except LibraryPartitionError as exc:
                raise SemanticChoiceError(str(exc)) from exc
            selected_set = set(selected)
            remainder = tuple(
                ref for ref in looked_refs if ref not in selected_set
            )
        if (
            len(selected) < int(effect["_effective_minimum"])
            or len(selected) > int(effect["_effective_maximum"])
        ):
            raise SemanticChoiceError(
                "Library selection cardinality is not authoritative"
            )
        policy = str(effect["selection_policy"])
        eligible = tuple(
            tuple(str(ref) for ref in group)
            for group in effect.get("_eligible_groups", ())
        )
        if policy == "fixed_any":
            valid_selection = all(ref in looked_refs for ref in selected)
        elif policy == "optional_slots":
            valid_selection = _slot_assignment_exists(selected, eligible)
        else:
            legal = set(eligible[0]) if eligible else set()
            valid_selection = all(ref in legal for ref in selected)
            if policy == "all_matching":
                valid_selection = valid_selection and set(selected) == legal
        if not valid_selection:
            raise SemanticChoiceError(
                "Selected cards do not satisfy the issued characteristics"
            )
        try:
            arrangement = LibrarySelectionArrangement(
                looked=looked,
                selected_refs=selected,
                remainder_refs=remainder,
                remainder_destination=str(effect["remainder_destination"]),
                remainder_order=str(effect["remainder_order"]),
            )
        except LibrarySelectionError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(
            intents=(
                self._intent(
                    effect,
                    arrangement,
                    stack_ref=continuation.stack_ref,
                ),
            )
        )


FIXED_LIBRARY_SELECTION_CHOICE_HANDLERS = (
    FixedLibrarySelectionChoiceHandler(),
)


__all__ = [
    "FIXED_LIBRARY_SELECTION_CHOICE_HANDLERS",
    "FixedLibrarySelectionChoiceHandler",
]
