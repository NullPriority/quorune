"""Choose one surviving original card from a committed library arrival group."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.library_creature_arrival import DEPENDENCIES, OPERATION
from ..semantic_runtime.intents import ZoneMoveIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _current_candidates(effect, actor, query):
    fields = {"op","schema_version","player","arrivals"}
    if (set(effect)-{"_stack_label"} != fields or effect["op"] != OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["player"] != actor
            or not isinstance(effect["arrivals"], (list,tuple)) or not effect["arrivals"]):
        raise SemanticChoiceError("The arrival result requires its original grouped occurrence and controller")
    known = set()
    current = {}
    for identity in effect["arrivals"]:
        if not isinstance(identity,Mapping) or set(identity)!={"ref","logical_id","counter"}:
            raise SemanticChoiceError("A library arrival must record a complete incarnation")
        ref, logical, counter = (identity[key] for key in ("ref","logical_id","counter"))
        if (type(ref) is not str or not ref or type(logical) is not str or not logical or ref in known
                or type(counter) is not int or counter<0):
            raise SemanticChoiceError("Library arrival references must be distinct original incarnations")
        known.add(ref)
        row = query.object(ref,zones=("graveyard",))
        if row is not None and row.owner==actor and row.logical_object_id==logical:
            current[ref]=(row,counter)
    return current


def _move(actor, ref, counter, label):
    return ZoneMoveIntent(
        actor=actor, object_ref=ref, expected_zones=("graveyard",),
        destination="battlefield", reason=label, owned_only=True, new_controller=actor,
        expected_zone_change_counter=counter,
    )


@dataclass(frozen=True, slots=True)
class LibraryCreatureArrivalChoiceHandler:
    operation: str = OPERATION
    handler_id: str = "choice.graveyard.grouped-library-arrival.v1"
    schema_version: int = 1
    capability_dependencies: tuple[str,...] = DEPENDENCIES
    rule_references: tuple[str,...] = ("CR 400.7", "CR 603.2c", "CR 603.3", "CR 608.2d")
    continuation_fields: tuple[str,...] = ("schema_version","player","arrivals","_stack_label")
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ("objects","legal_actions","prompt")
    mutation_path: tuple[str,...] = ("ZoneMoveIntent","ZoneTransitionOwner.move_card")
    replay_fixture: str = "grouped-library-creature-arrival"
    test_modules: tuple[str,...] = ("tests.test_public_state_graveyard_arrival_actions",)

    def prepare(self,effect,context):
        if context.actor != context.stack_controller:
            raise SemanticChoiceError("An arrival result belongs to its original resolving controller")
        options = _current_candidates(effect, context.actor, context.query)
        if len(options) <= 1:
            intents = tuple(_move(context.actor,ref,counter,context.stack_label)
                            for ref,(_row,counter) in options.items())
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
                auto_continue=AutoContinue(reason="Apply the only surviving original creature arrival."),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose one of the creature cards that arrived in your graveyard.",
                choice=ObjectChoice(field_name="card",legal_refs=tuple(options),zones=("graveyard",),
                                    minimum=1,maximum=1,owner_relation="actor"),
                public_context=FrozenMap({"stack":context.stack_ref,"objects":[
                    {"id":ref,"name":row.printed_name} for ref,(row,_) in options.items()
                ]}),
            ),
            continuation_effect=FrozenMap({**effect,"_stack_label":context.stack_label}),
        )

    def complete(self,continuation,response,query):
        effect=continuation.effect
        actor=continuation.semantic_frame.controller
        options=_current_candidates(effect,actor,query)
        chosen=response.get("card")
        if type(chosen) is not str or chosen not in options:
            raise SemanticChoiceError("Choose a still-present creature arrival from this occurrence")
        return SemanticChoiceCompletion(intents=(_move(actor,chosen,options[chosen][1],effect["_stack_label"]),))
