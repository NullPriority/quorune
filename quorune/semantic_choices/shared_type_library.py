"""Private top-card look and a separate matching optional reveal."""
from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap
from ..shared_creature_type_model import SharedCreatureTypeObject, canonical_creature_types
from ..rules.shared_type_library_model import (
    SHARED_TYPE_LIBRARY_OPERATION, SHARED_TYPE_LIBRARY_CAPABILITY,
    SharedCreatureTypeComparison, SharedTypeLibraryResult,
)
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from .model import (AutoContinue, ScalarChoice, SemanticChoiceCompletion,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest)

_BASE = {"op", "player", "result"}
_STAGES = {"look": set(), "reveal": {"_top"}}


def _decode(effect, actor, *, continuation=False):
    stage = effect.get("_shared_type_stage", "look")
    allowed = _BASE | ({"_shared_type_stage"} if "_shared_type_stage" in effect else set())
    if "_comparison" in effect:
        allowed.add("_comparison")
    if stage not in _STAGES or set(effect) != allowed | _STAGES[stage]:
        raise ValueError("Shared-type library instruction has unknown stage fields")
    if effect.get("op") != SHARED_TYPE_LIBRARY_OPERATION or effect.get("player") != actor:
        raise ValueError("Shared-type library chooser must be the resolving controller")
    if continuation and stage == "look" and "_shared_type_stage" not in effect:
        raise ValueError("Shared-type library continuation lacks its explicit stage")
    if stage == "reveal":
        top = effect["_top"]
        if (not isinstance(top, (dict, FrozenMap)) or set(top) != {"ref", "logical_object_id"}
                or any(type(top[k]) is not str or not top[k] for k in top)):
            raise ValueError("Shared-type looked-at identity is malformed")
    if "_comparison" in effect:
        comparison = SharedCreatureTypeComparison.from_dict(effect["_comparison"])
        if stage != "reveal":
            raise ValueError("Retained reveal eligibility is malformed")
    if continuation and stage == "reveal" and "_comparison" not in effect:
        raise ValueError("Reveal continuation lacks its evaluated type comparison")
    return SharedTypeLibraryResult.from_dict(effect["result"]), stage


def _top(query, actor, expected=None):
    refs = query.library_refs(actor, top_first=True)
    if not refs:
        return None
    row = query.object(refs[0], zones=("library",))
    if row is None or row.owner != actor or row.phased_out:
        raise ValueError("Private library top is unavailable")
    if expected is not None:
        if (not isinstance(expected, dict) and not isinstance(expected, FrozenMap)
                or set(expected) != {"ref", "logical_object_id"}
                or row.ref != expected["ref"] or row.logical_object_id != expected["logical_object_id"]):
            raise ValueError("Looked-at library incarnation changed")
    return row


def _comparison(query, top):
    source = getattr(query, "shared_type_library_source", None)
    if not isinstance(source, SharedCreatureTypeObject):
        raise ValueError("Shared-type library source information is unavailable")
    candidate = SharedCreatureTypeObject(
        ContinuousObjectIdentity(top.object_id, top.logical_object_id),
        canonical_creature_types(top.subtypes))
    return SharedCreatureTypeComparison(source, candidate)


@dataclass(frozen=True, slots=True)
class SharedTypeLibraryHandler:
    operation: str = SHARED_TYPE_LIBRARY_OPERATION
    handler_id: str = "choice.library.shared-type-reveal.v1"
    schema_version: int = 1
    rule_references: tuple = ("207.2c", "608.2h", "701.20a", "701.20b", "701.20c", "701.20e")
    capability_dependencies: tuple = (SHARED_TYPE_LIBRARY_CAPABILITY,)
    continuation_fields: tuple = ("player", "result", "_shared_type_stage", "_top", "_comparison")
    private_data: tuple = ("looked card identity", "matching creature types")
    projected_fields: tuple = ("prompt", "objects", "legal_actions.choice_schema")
    mutation_path: tuple = ("RevealLibraryCardsIntent", "SemanticChoiceCompletion.prepend_effects")
    replay_fixture: str = "private-top-shared-type-reveal"
    test_modules: tuple = ("tests.test_shared_creature_type_quantities",)

    def prepare(self, effect, context):
        try:
            spec, stage = _decode(effect, context.actor)
            if context.actor != context.stack_controller:
                raise ValueError("Shared-type library actor differs from ability controller")
            if context.actor not in context.query.active_seats:
                return SemanticChoicePreparation(None, FrozenMap(effect),
                    auto_continue=AutoContinue(reason="Library chooser left the game"))
            top = _top(context.query, context.actor, effect.get("_top") if stage == "reveal" else None)
            if top is None:
                return SemanticChoicePreparation(None, FrozenMap(effect),
                    auto_continue=AutoContinue(reason="Library is empty"))
            if stage == "look":
                return SemanticChoicePreparation(
                    SemanticChoiceRequest(prompt="Look at the top card of your library?",
                        choice=ScalarChoice(field_name="choice", legal_values=("look", "decline")),
                        public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation})),
                    FrozenMap({**effect, "_shared_type_stage": "look"}))
            comparison = (SharedCreatureTypeComparison.from_dict(effect["_comparison"])
                if "_comparison" in effect else _comparison(context.query, top))
            # Keep an actor-private acknowledgment on both paths so public progress
            # cannot disclose whether the hidden card matched.
            return SemanticChoicePreparation(
                SemanticChoiceRequest(prompt="Reveal the matching card?" if comparison.matches else "Continue after looking.",
                    choice=ScalarChoice(field_name="choice",
                        legal_values=("reveal", "decline") if comparison.matches else ("decline",)),
                    public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation,
                        "objects": [{"id": top.ref, "name": top.printed_name}]})),
                FrozenMap({**effect, "_comparison": comparison.to_dict()}))
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        try:
            effect = continuation.effect
            actor = continuation.semantic_frame.controller
            spec, stage = _decode(effect, actor, continuation=True)
            if actor not in query.active_seats:
                raise ValueError("Library chooser left the game")
            choice = response.get("choice")
            if choice == "decline":
                return SemanticChoiceCompletion()
            top = _top(query, actor, effect.get("_top") if stage == "reveal" else None)
            if top is None:
                raise ValueError("Library top disappeared")
            if stage == "look":
                if choice != "look":
                    raise ValueError("Choose look or decline")
                next_effect = {**dict(effect), "_shared_type_stage": "reveal",
                    "_top": {"ref": top.ref, "logical_object_id": top.logical_object_id}}
                return SemanticChoiceCompletion(
                    intents=(RevealLibraryCardsIntent(actor, actor, actor, (top.ref,),
                        continuation.note, public=False),),
                    prepend_effects=(FrozenMap(next_effect),))
            comparison = SharedCreatureTypeComparison.from_dict(effect["_comparison"])
            expected = getattr(query, "shared_type_library_source", None)
            if (choice != "reveal" or not comparison.matches
                    or not isinstance(expected, SharedCreatureTypeObject)
                    or comparison.source.identity != expected.identity
                    or comparison.candidate.identity != ContinuousObjectIdentity(top.object_id, top.logical_object_id)):
                raise ValueError("Only the evaluated matching looked-at card can be revealed")
            return SemanticChoiceCompletion(
                intents=(RevealLibraryCardsIntent(actor, actor, actor, (top.ref,),
                    continuation.note, public=True),),
                prepend_effects=spec.effects)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
