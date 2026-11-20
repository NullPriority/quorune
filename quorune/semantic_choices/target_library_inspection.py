"""A private look by the resolving actor over a separately owned library."""
from dataclasses import dataclass
from ..target_library_inspection_model import (
    TARGET_LIBRARY_INSPECTION_OPERATION, TARGET_LIBRARY_INSPECTION_CAPABILITY,
    TARGET_LIBRARY_INSPECTION_FIELDS, decode_target_library_inspection,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import RevealLibraryCardsIntent, ZoneMoveIntent
from .model import (ScalarChoice, ObjectChoice, AutoContinue, SemanticChoiceRequest,
    SemanticChoicePreparation, SemanticChoiceCompletion, SemanticChoiceError)


@dataclass(frozen=True, slots=True)
class TargetLibraryInspectionHandler:
    operation: str = TARGET_LIBRARY_INSPECTION_OPERATION
    handler_id: str = "choice.library.fixed-target-inspection.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("401.2", "608.2b", "608.2c", "608.2d", "608.2h", "701.20e")
    capability_dependencies: tuple[str, ...] = (TARGET_LIBRARY_INSPECTION_CAPABILITY, "target.revalidate_resolution")
    continuation_fields: tuple[str, ...] = ("player", "target", "inspection", "_choice_actor", "_looked", "_stack_label")
    private_data: tuple[str, ...] = ("looked target library identities",)
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "ZoneMoveIntent", "ZoneTransitionOwner")
    replay_fixture: str = "fixed-target-library-inspection"
    test_modules: tuple[str, ...] = ("tests.test_target_library_inspection",)

    def prepare(self, effect, context):
        try:
            spec = decode_target_library_inspection(effect, actor=context.actor)
            if context.actor != context.stack_controller:raise ValueError("Inspection actor differs from the resolving controller")
            target = effect["target"]
            if spec.opponent_only and target == context.actor:raise ValueError("Inspection requires an opponent target")
            if target not in context.query.active_seats:
                return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect), auto_continue=AutoContinue(reason="original target is unavailable"))
            refs = context.query.library_refs(target, top_first=True)[:spec.count]
            rows = tuple(context.query.object(ref, zones=("library",)) for ref in refs)
            if any(row is None or row.owner != target for row in rows):raise ValueError("Inspected library window was not authorized")
            if not refs:
                return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect), auto_continue=AutoContinue(reason="target library is empty"))
            if spec.mode == "look":
                choice = ScalarChoice(field_name="choice", legal_values=("continue",));prompt = "Continue after looking at these cards."
            elif spec.mode == "may_graveyard":
                choice = ScalarChoice(field_name="choice", legal_values=("keep", "graveyard"));prompt = "Keep the card on top or put it into its owner's graveyard."
            else:
                choice = ObjectChoice(field_name="cards", legal_refs=tuple(refs), zones=("library",), minimum=1, maximum=1, visibility="actor_private")
                prompt = "Choose one of the cards you looked at."
            return SemanticChoicePreparation(
                request=SemanticChoiceRequest(prompt=prompt, choice=choice, public_context=FrozenMap({"stack":context.stack_ref,
                    "operation":self.operation,"objects":[{"id":row.ref,"name":row.printed_name} for row in rows]})),
                continuation_effect=FrozenMap({**dict(effect),"_choice_actor":context.actor,
                    "_looked":[{"ref":row.ref,"logical_object_id":row.logical_object_id} for row in rows],"_stack_label":context.stack_label}),
                preparation_intents=(RevealLibraryCardsIntent(actor=context.actor, player=target, viewer=context.actor,
                    refs_top_first=tuple(refs), reason=context.stack_label, public=False),))
        except (TypeError, ValueError, KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        effect = continuation.effect;actor = effect.get("_choice_actor")
        try:
            if type(actor) is not str or not actor:raise ValueError("Inspection actor is missing")
            spec = decode_target_library_inspection({key:effect[key] for key in TARGET_LIBRARY_INSPECTION_FIELDS}, actor=actor)
            target = effect["target"];looked = effect["_looked"]
            if target not in query.active_seats or not isinstance(looked,(list,tuple)) or not looked:raise ValueError("Inspected player or identities are unavailable")
            refs = tuple(identity["ref"] for identity in looked)
            if refs != query.library_refs(target, top_first=True)[:spec.count]:raise ValueError("Inspected library top changed")
            for identity in looked:
                if set(identity) != {"ref","logical_object_id"}:raise ValueError("Inspection identity is malformed")
                row = query.object(identity["ref"], zones=("library",))
                if row is None or row.owner != target or row.logical_object_id != identity["logical_object_id"]:raise ValueError("Inspected card incarnation changed")
            if spec.mode == "look":
                if response.get("choice") != "continue":raise ValueError("Continue the private look")
                return SemanticChoiceCompletion()
            if spec.mode == "may_graveyard":
                choice = response.get("choice")
                if choice not in {"keep","graveyard"}:raise ValueError("Choose keep or graveyard")
                if choice == "keep":return SemanticChoiceCompletion()
                selected = refs[0];destination = "graveyard"
            else:
                cards = response.get("cards")
                if not isinstance(cards,(list,tuple)) or len(cards) != 1 or cards[0] not in refs:raise ValueError("Choose exactly one original inspected card")
                selected = cards[0];destination = "exile" if spec.mode == "one_exile" else "graveyard"
            return SemanticChoiceCompletion(intents=(ZoneMoveIntent(actor=actor, object_ref=selected, expected_zones=("library",),
                destination=destination, reason=effect["_stack_label"], owned_only=False, controlled_only=False),))
        except (TypeError, ValueError, KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
