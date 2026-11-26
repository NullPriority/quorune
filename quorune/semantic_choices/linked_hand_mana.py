from dataclasses import dataclass

from ..rules.linked_hand_mana_model import CAPABILITY, CHOICE, PAIR, REQUIREMENTS, imprint_pair, imprintable_types
from ..replacement.immutable import FrozenMap
from .model import (AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)


def _eligible(query, actor):
    return tuple(sorted((row for row in query.objects(zones=("hand",), owner=actor)
                         if imprintable_types(row.types)), key=lambda row: row.ref))


def _identities(rows):
    return {row.ref: row.logical_object_id for row in rows}


@dataclass(frozen=True, slots=True)
class LinkedHandImprintChoice:
    operation: str = CHOICE
    handler_id: str = "choice.hand.linked-mana-imprint.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("400.7", "406.3", "607.2a", "607.3")
    capability_dependencies: tuple[str, ...] = REQUIREMENTS
    continuation_fields: tuple[str, ...] = ("op", "schema_version", "player", PAIR, "_hand")
    private_data: tuple[str, ...] = ("current eligible cards in controller hand",)
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("exile_for_linked_ability", "ZoneTransitionOwner.move_cards_simultaneously")
    replay_fixture: str = "linked-hand-mana-imprint"
    test_modules: tuple[str, ...] = ("tests.test_chrome_mox",)

    def prepare(self, effect, context):
        actor = context.actor
        try:
            imprint_pair(effect, actor=actor)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("Linked hand imprint requires its resolving controller")
        rows = _eligible(context.query, actor)
        if not rows:
            return SemanticChoicePreparation(None, FrozenMap(effect),
                auto_continue=AutoContinue(reason="No eligible card can be imprinted"))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="You may exile a nonartifact, nonland card from your hand.",
                choice=ObjectChoice("cards", tuple(row.ref for row in rows), ("hand",), minimum=0, maximum=1,
                                    optional=True, visibility="actor_private", owner_relation="actor"),
                public_context=FrozenMap({"stack": context.stack_ref,
                    "objects": [{"id": row.ref, "name": row.printed_name} for row in rows]})),
            continuation_effect=FrozenMap({**effect, "_hand": _identities(rows)}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = continuation.semantic_frame.controller
        try:
            pair = imprint_pair({key: value for key, value in effect.items() if key != "_hand"}, actor=actor)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        current = _identities(_eligible(query, actor))
        refs = response.get("cards")
        if (actor not in query.active_seats or current != effect.get("_hand")
                or not isinstance(refs, (list, tuple)) or len(refs) > 1
                or any(type(ref) is not str or ref not in current for ref in refs)):
            raise SemanticChoiceError("Choose one current eligible hand card or decline")
        if not refs:
            return SemanticChoiceCompletion()
        card, = refs
        return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
            "op": "exile", PAIR: pair, "card": card, "incarnation": current[card],
            "imprint_stack": continuation.stack_ref,
        }),))
