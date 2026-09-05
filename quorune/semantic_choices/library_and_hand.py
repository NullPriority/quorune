from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import (
    ChooseOneRestBottomRandomIntent,
    DrawCardsIntent,
    ExploreCompletedIntent,
    MoveObjectsSimultaneouslyIntent,
    PayLifeIntent,
    PlaceCountersIntent,
    RecordChoiceIntent,
    ReturnCardsToLibraryTopIntent,
    RevealLibraryCardsIntent,
    ZoneMoveIntent,
)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    DecisionMapChoice,
    ObjectChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _private_cards(rows: tuple[Any, ...]) -> list[dict[str, str]]:
    return [{"id": row.ref, "name": row.printed_name} for row in rows]


@dataclass(frozen=True, slots=True)
class ExploreChoiceHandler:
    operation: str = "explore"
    handler_id: str = "choice.library.explore.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 701.44a",
        "CR 701.44b",
        "CR 701.44c",
    )
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "card",
        "_choice_actor",
        "_top_ref",
        "_explorer_ref",
        "_explorer_logical_object_id",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "card",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "RevealLibraryCardsIntent",
        "PlaceCountersIntent",
        "ZoneMoveIntent",
        "ExploreCompletedIntent",
    )
    replay_fixture: str = "semantic-choice-explore"
    test_modules: tuple[str, ...] = ("tests.test_exact_zimone_closure",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        explorer_ref = str(effect.get("card") or "")
        explorer = context.query.object(explorer_ref)
        if explorer is None:
            raise SemanticChoiceError("The exploring permanent is unavailable")
        explorer_logical_object_id = (
            context.source_logical_object_id
            if context.source_ref == explorer.ref
            and context.source_logical_object_id is not None
            else explorer.logical_object_id
        )
        explorer_is_current = bool(
            explorer.zone == "battlefield"
            and not explorer.phased_out
            and explorer.logical_object_id == explorer_logical_object_id
        )
        library = context.query.library_refs(context.actor, top_first=True)
        if not library:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                preparation_intents=(
                    ExploreCompletedIntent(
                        actor=context.actor,
                        player=context.actor,
                        explorer_ref=explorer.ref,
                        explorer_logical_object_id=(
                            explorer_logical_object_id
                        ),
                        result="empty_library",
                        reason=context.stack_label,
                    ),
                ),
                auto_continue=AutoContinue(reason="library is empty"),
            )
        top_ref = library[0]
        top = context.query.object(top_ref, zones=("library",))
        if top is None:
            raise SemanticChoiceError("The library top is unavailable")
        reveal = RevealLibraryCardsIntent(
            actor=context.actor,
            player=context.actor,
            viewer=context.actor,
            refs_top_first=(top.ref,),
            reason=context.stack_label,
            public=True,
        )
        if "land" in top.types:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                preparation_intents=(
                    reveal,
                    ZoneMoveIntent(
                        actor=context.actor,
                        object_ref=top.ref,
                        expected_zones=("library",),
                        destination="hand",
                        reason="explore",
                        owned_only=True,
                    ),
                    ExploreCompletedIntent(
                        actor=context.actor,
                        player=context.actor,
                        explorer_ref=explorer.ref,
                        explorer_logical_object_id=(
                            explorer_logical_object_id
                        ),
                        result="land_revealed",
                        reason=context.stack_label,
                        revealed_card_ref=top.ref,
                    ),
                ),
                auto_continue=AutoContinue(reason="explore revealed a land"),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Put the revealed nonland card into your graveyard "
                    "or leave it on top."
                ),
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=("graveyard", "top"),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "card": {"id": top.ref, "name": top.printed_name},
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_top_ref": top.ref,
                    "_explorer_ref": explorer.ref,
                    "_explorer_logical_object_id": (
                        explorer_logical_object_id
                    ),
                    "_stack_label": context.stack_label,
                }
            ),
            preparation_intents=(
                reveal,
                *(
                    (
                        PlaceCountersIntent(
                            actor=context.actor,
                            object_refs=(explorer.ref,),
                            counter_name="+1/+1",
                            amount=1,
                            reason=context.stack_label,
                            source_ref=explorer.ref,
                        ),
                    )
                    if explorer_is_current
                    else ()
                ),
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        choice = str(response.get("choice") or "")
        if choice not in {"graveyard", "top"}:
            raise SemanticChoiceError(
                "Explore requires choosing graveyard or top"
            )
        effect = continuation.effect
        actor = str(effect["_choice_actor"])
        top_ref = str(effect["_top_ref"])
        library = query.library_refs(actor, top_first=True)
        if not library or library[0] != top_ref:
            raise SemanticChoiceError(
                "The revealed explore card is no longer on top"
            )
        intents: list[Any] = []
        if choice == "graveyard":
            intents.append(
                ZoneMoveIntent(
                    actor=actor,
                    object_ref=top_ref,
                    expected_zones=("library",),
                    destination="graveyard",
                    reason="explore",
                    owned_only=True,
                )
            )
        intents.append(
            ExploreCompletedIntent(
                actor=actor,
                player=actor,
                explorer_ref=str(effect["_explorer_ref"]),
                explorer_logical_object_id=str(
                    effect["_explorer_logical_object_id"]
                ),
                result=(
                    "nonland_graveyard_choice"
                    if choice == "graveyard"
                    else "nonland_top_choice"
                ),
                reason=str(effect["_stack_label"]),
                revealed_card_ref=top_ref,
            )
        )
        return SemanticChoiceCompletion(intents=tuple(intents))


@dataclass(frozen=True, slots=True)
class FomoriVaultChoiceHandler:
    operation: str = "fomori_vault"
    handler_id: str = "choice.library.choose-one-rest-random-bottom.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 701.16", "CR 701.20")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "_choice_actor",
        "_looked_refs",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("looked library cards",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "cards",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "RevealLibraryCardsIntent",
        "ChooseOneRestBottomRandomIntent",
    )
    replay_fixture: str = "semantic-choice-fomori-vault"
    test_modules: tuple[str, ...] = ("tests.test_exact_mishra_closure",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        artifacts = tuple(
            row
            for row in context.query.objects(
                zones=("battlefield",),
                controller=context.actor,
            )
            if "artifact" in row.types
        )
        looked = context.query.library_refs(
            context.actor,
            top_first=True,
        )[: len(artifacts)]
        if not looked:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no cards were looked at"),
            )
        rows = tuple(
            row
            for ref in looked
            for row in [context.query.object(ref, zones=("library",))]
            if row is not None
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Choose one looked-at card to put into your hand. "
                    "The rest go to the bottom at random."
                ),
                choice=ObjectChoice(
                    field_name="card",
                    legal_refs=tuple(looked),
                    zones=("library",),
                    visibility="actor_private",
                    owner_relation="actor",
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "cards": _private_cards(rows),
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_looked_refs": looked,
                    "_stack_label": context.stack_label,
                }
            ),
            preparation_intents=(
                RevealLibraryCardsIntent(
                    actor=context.actor,
                    player=context.actor,
                    viewer=context.actor,
                    refs_top_first=tuple(looked),
                    reason=context.stack_label,
                ),
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        selected = str(response.get("card") or "")
        looked = tuple(str(value) for value in effect.get("_looked_refs", ()))
        if selected not in looked:
            raise SemanticChoiceError("Choose one of the looked-at cards")
        actor = str(effect["_choice_actor"])
        if any(
            query.object(ref, zones=("library",)) is None for ref in looked
        ):
            raise SemanticChoiceError("A looked-at card left the library")
        return SemanticChoiceCompletion(
            intents=(
                ChooseOneRestBottomRandomIntent(
                    actor=actor,
                    player=actor,
                    chosen_ref=selected,
                    looked_refs=looked,
                    reason="Fomori Vault selection",
                    source_stack_ref=continuation.stack_ref,
                    event_code="fomori_vault.select",
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class DiscardDrawChoiceHandler:
    operation: str = "discard_draw_up_to"
    handler_id: str = "choice.hand.discard-draw-up-to.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 701.8", "CR 121")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "maximum",
        "_choice_actor",
        "_legal_refs",
        "_maximum",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("hand",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "cards",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "MoveObjectsSimultaneouslyIntent",
        "typed draw continuation",
    )
    replay_fixture: str = "semantic-choice-discard-draw"
    test_modules: tuple[str, ...] = ("tests.test_exact_mishra_closure",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        maximum = max(0, int(effect.get("maximum", 2)))
        rows = context.query.objects(zones=("hand",), owner=context.actor)
        if not rows or maximum == 0:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no discard is possible"),
            )
        maximum = min(maximum, len(rows))
        refs = tuple(row.ref for row in rows)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    f"Discard up to {maximum} card(s), then draw that "
                    "many cards."
                ),
                choice=ObjectChoice(
                    field_name="cards",
                    legal_refs=refs,
                    zones=("hand",),
                    minimum=0,
                    maximum=maximum,
                    optional=True,
                    visibility="actor_private",
                    owner_relation="actor",
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "cards": _private_cards(rows),
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_legal_refs": refs,
                    "_maximum": maximum,
                    "_stack_label": context.stack_label,
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        selected = tuple(str(value) for value in response.get("cards", ()))
        legal = {str(value) for value in effect.get("_legal_refs", ())}
        maximum = int(effect.get("_maximum", 0))
        if (
            len(selected) != len(set(selected))
            or len(selected) > maximum
            or any(ref not in legal for ref in selected)
        ):
            raise SemanticChoiceError("Discard selection is not authoritative")
        actor = str(effect["_choice_actor"])
        if any(query.object(ref, zones=("hand",)) is None for ref in selected):
            raise SemanticChoiceError("A selected hand card is unavailable")
        intents: list[Any] = []
        if selected:
            intents.append(
                MoveObjectsSimultaneouslyIntent(
                    actor=actor,
                    object_refs=selected,
                    expected_zones=("hand",),
                    destination="graveyard",
                    reason=str(effect["_stack_label"]),
                    transition_kind=ZoneTransitionKind.DISCARD,
                    owned_only=True,
                )
            )
        intents.append(
            RecordChoiceIntent(
                actor=actor,
                event_code="daretti.discard_draw",
                message=(
                    f"{actor} discarded {len(selected)} card(s) and will "
                    "draw that many."
                ),
                details=FrozenMap(
                    {"count": len(selected), "objects": selected}
                ),
                visibility=(actor, "analyst"),
                importance=2,
                changed_players=(actor,),
            )
        )
        return SemanticChoiceCompletion(
            intents=tuple(intents),
            prepend_effects=(
                FrozenMap(
                    {
                        "op": "draw",
                        "player": actor,
                        "count": len(selected),
                        "private": True,
                        "reason": str(effect["_stack_label"]),
                    }
                ),
            )
            if selected
            else (),
        )


@dataclass(frozen=True, slots=True)
class SylvanLibraryChoiceHandler:
    operation: str = "sylvan_library_settle"
    handler_id: str = "choice.hand.sylvan-settlement.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 121.1", "CR 118.4")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "_choice_actor",
        "_eligible_refs",
        "_required",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("cards drawn this turn", "hand")
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema",
    )
    mutation_path: tuple[str, ...] = (
        "ReturnCardsToLibraryTopIntent",
        "PayLifeIntent",
    )
    replay_fixture: str = "semantic-choice-sylvan-library"
    test_modules: tuple[str, ...] = ("tests.test_exact_zimone_closure",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        hand = {
            row.ref: row
            for row in context.query.objects(
                zones=("hand",), owner=context.actor
            )
        }
        eligible = tuple(
            dict.fromkeys(
                ref
                for ref in context.query.drawn_this_turn(context.actor)
                if ref in hand
            )
        )
        required = min(2, len(eligible))
        if required == 0:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no eligible drawn cards"),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    f"Choose {required} card(s) drawn this turn; for each, "
                    "pay 4 life or put it on top of your library."
                ),
                choice=DecisionMapChoice(
                    field_name="decisions",
                    legal_refs=eligible,
                    required=required,
                    legal_values=("pay_life", "top"),
                    companion_schema=FrozenMap(
                        {
                            "top_order": {
                                "field": "top_order",
                                "required_when_value": "top",
                                "contains": (
                                    "exactly every ref mapped to top"
                                ),
                                "order": "top-first",
                            },
                            "example": {
                                "decisions": {
                                    ref: (
                                        "pay_life"
                                        if index == 0
                                        else "top"
                                    )
                                    for index, ref in enumerate(
                                        eligible[:required]
                                    )
                                },
                                "top_order": list(eligible[1:required]),
                            },
                        }
                    ),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "objects": _private_cards(
                            tuple(hand[ref] for ref in eligible)
                        ),
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_eligible_refs": eligible,
                    "_required": required,
                    "_stack_label": context.stack_label,
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        raw = response.get("decisions") or {}
        if not isinstance(raw, Mapping):
            raise SemanticChoiceError("Sylvan decisions must be an object map")
        decisions = {str(key): str(value) for key, value in raw.items()}
        eligible = {str(value) for value in effect.get("_eligible_refs", ())}
        required = int(effect.get("_required", 0))
        if (
            len(decisions) != required
            or any(ref not in eligible for ref in decisions)
            or any(value not in {"pay_life", "top"} for value in decisions.values())
        ):
            raise SemanticChoiceError(
                "Library settlement requires an exact decision for each card"
            )
        actor = str(effect["_choice_actor"])
        life_cards = tuple(
            ref for ref, value in decisions.items() if value == "pay_life"
        )
        life_cost = 4 * len(life_cards)
        if query.player_life(actor) < life_cost:
            raise SemanticChoiceError("The selected life payment is not payable")
        top_cards = {
            ref for ref, value in decisions.items() if value == "top"
        }
        top_order = tuple(str(value) for value in response.get("top_order", ()))
        if len(top_order) != len(top_cards) or set(top_order) != top_cards:
            raise SemanticChoiceError(
                "top_order must list every returned card exactly once"
            )
        if any(
            query.object(ref, zones=("hand",)) is None
            for ref in (*life_cards, *top_order)
        ):
            raise SemanticChoiceError("A selected hand card is unavailable")
        intents: list[Any] = []
        if top_order:
            intents.append(
                ReturnCardsToLibraryTopIntent(
                    actor=actor,
                    player=actor,
                    refs_top_first=top_order,
                    reason="return selected draws to the library",
                )
            )
        if life_cost:
            intents.append(
                PayLifeIntent(
                    actor=actor,
                    player=actor,
                    amount=life_cost,
                    reason=str(effect["_stack_label"]),
                )
            )
        intents.append(
            RecordChoiceIntent(
                actor=actor,
                event_code="sylvan_library.settle",
                message=(
                    f"{actor} paid {life_cost} life and returned "
                    f"{len(top_order)} card(s) to the top of their library."
                ),
                details=FrozenMap(
                    {
                        "paid_life_for": life_cards,
                        "life_paid": life_cost,
                        "top_order": top_order,
                    }
                ),
                visibility=(actor, "analyst"),
                importance=2,
                changed_players=(actor,),
            )
        )
        return SemanticChoiceCompletion(intents=tuple(intents))


@dataclass(frozen=True, slots=True)
class DrawOptionalLandChoiceHandler:
    operation: str = "draw_optional_land"
    handler_id: str = "choice.hand.draw-optional-land.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 121", "CR 701.14")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "repeat_if_land_count",
        "stage",
        "_choice_actor",
        "_legal_refs",
        "_repeat",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("drawn card", "hand")
    projected_fields: tuple[str, ...] = (
        "prompt",
        "options",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "DrawCardsIntent",
        "ZoneMoveIntent",
    )
    replay_fixture: str = "semantic-choice-draw-optional-land"
    test_modules: tuple[str, ...] = ("tests.test_exact_zimone_closure",)

    @staticmethod
    def _repeat_effect(effect: Mapping[str, Any]) -> FrozenMap:
        repeated = dict(effect)
        repeated.pop("stage", None)
        repeated.pop("_repeat", None)
        repeated.pop("repeat_if_land_count", None)
        return FrozenMap(repeated)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        stage = str(effect.get("stage") or "draw")
        if stage == "draw":
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                preparation_intents=(
                    DrawCardsIntent(
                        player=context.actor,
                        count=1,
                        reason=context.stack_label,
                        private=False,
                    ),
                ),
                auto_continue=AutoContinue(
                    reason="draw completed before land selection",
                    prepend_effects=(
                        FrozenMap({**dict(effect), "stage": "select"}),
                    ),
                ),
            )
        if stage != "select":
            raise SemanticChoiceError("Unknown optional-land choice stage")
        rows = tuple(
            row
            for row in context.query.objects(
                zones=("hand",), owner=context.actor
            )
            if "land" in row.types
        )
        threshold = effect.get("repeat_if_land_count")
        land_count = sum(
            1
            for row in context.query.objects(
                zones=("battlefield",), controller=context.actor
            )
            if "land" in row.types
        )
        repeat = bool(threshold and land_count >= int(threshold))
        if not rows:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason="no land is available",
                    prepend_effects=(self._repeat_effect(effect),)
                    if repeat
                    else (),
                ),
            )
        refs = tuple(row.ref for row in rows)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "You may put a land card from your hand onto the "
                    "battlefield tapped."
                ),
                choice=ObjectChoice(
                    field_name="card",
                    legal_refs=refs,
                    zones=("hand",),
                    minimum=0,
                    maximum=1,
                    optional=True,
                    visibility="actor_private",
                    owner_relation="actor",
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "optional": True,
                        "options": refs,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_legal_refs": refs,
                    "_repeat": repeat,
                    "_stack_label": context.stack_label,
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        actor = str(effect["_choice_actor"])
        selected = response.get("card")
        intents: tuple[Any, ...] = ()
        if selected not in {None, ""}:
            ref = str(selected)
            legal = {str(value) for value in effect.get("_legal_refs", ())}
            if ref not in legal or query.object(ref, zones=("hand",)) is None:
                raise SemanticChoiceError("Selected land is not legal")
            intents = (
                ZoneMoveIntent(
                    actor=actor,
                    object_ref=ref,
                    expected_zones=("hand",),
                    destination="battlefield",
                    reason=str(effect["_stack_label"]),
                    required_types=("land",),
                    owned_only=True,
                    new_controller=actor,
                    tapped_policy="tapped",
                ),
            )
        return SemanticChoiceCompletion(
            intents=intents,
            repeat_effect=(
                self._repeat_effect(effect)
                if bool(effect.get("_repeat"))
                else None
            ),
        )


LIBRARY_AND_HAND_CHOICE_HANDLERS = (
    ExploreChoiceHandler(),
    FomoriVaultChoiceHandler(),
    DiscardDrawChoiceHandler(),
    SylvanLibraryChoiceHandler(),
    DrawOptionalLandChoiceHandler(),
)
