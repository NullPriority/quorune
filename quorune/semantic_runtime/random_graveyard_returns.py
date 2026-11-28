from __future__ import annotations

"""Random graveyard selection followed by the canonical simultaneous move port."""

from dataclasses import dataclass
from ..rules.random_graveyard_returns import (
    OPERATION, CAPABILITY, EXILE_CAST_OPERATION, EXILE_CAST_CAPABILITY,
    RandomGraveyardReturnSpec, RandomGraveyardReturnIntent,
    random_exile_cast_instruction, resolve_random_graveyard_return, validate_random_exile_cast_instruction,
)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticNodeError
from .intents import IntentPlan,MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True,slots=True)
class RandomGraveyardReturnHandler:
    operation: str=OPERATION
    handler_id: str='generic.random-own-graveyard-return.v1'
    schema_version: int=1
    family: str=CAPABILITY
    rule_references: tuple[str,...]=('101.3','400.7','608.2c','608.2h')
    capability_dependencies: tuple[str,...]=(CAPABILITY,)

    def lower(self,effect,context):
        if self.operation == EXILE_CAST_OPERATION:
            return self._lower_exile_cast(effect, context)
        if (set(effect)-{'op','actor','count','selection','reason','_replacement_selections'}
                or not {'op','actor','count','selection'}<=set(effect) or effect['op']!=OPERATION
                or effect['actor']!=context.actor or context.source is None):
            raise SemanticNodeError('Random graveyard return requires its owned resolving instruction')
        try:intent=RandomGraveyardReturnIntent(context.actor,effect['count'],RandomGraveyardReturnSpec.from_dict(effect['selection']),
            f'{context.source.stack_ref}|{context.source.instruction_pointer}',str(effect.get('reason') or context.default_reason),effect.get('_replacement_selections',()))
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        if intent.selection.exile_for_cast:
            raise SemanticNodeError("The legacy return cannot acquire random exile authority")
        return IntentPlan(operation=self.operation,handler_id=self.handler_id,intents=(intent,))

    def _lower_exile_cast(self, effect, context):
        raw = {key: value for key, value in effect.items() if key not in {"reason", "_replacement_selections"}}
        if context.source is None or raw.get("player") != context.actor:
            raise SemanticNodeError("Random graveyard exile requires its resolving controller")
        try:
            selection = validate_random_exile_cast_instruction(raw, resolved=True)
            intent = RandomGraveyardReturnIntent(
                context.actor, 1, selection, f"{context.source.stack_ref}|{context.source.instruction_pointer}",
                str(effect.get("reason") or context.default_reason), effect.get("_replacement_selections", ()),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


RANDOM_GRAVEYARD_EXILE_CAST_HANDLER = RandomGraveyardReturnHandler(
    operation=EXILE_CAST_OPERATION, handler_id="generic.random-own-graveyard-exile-cast.v1",
    family=EXILE_CAST_CAPABILITY, capability_dependencies=(EXILE_CAST_CAPABILITY,),
)


def execute_random_graveyard_return(sink,intent):
    if intent.selection.exile_for_cast:
        return _execute_random_exile_cast(sink, intent)

    def commit(refs):
        return sink.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(actor=intent.actor,
            object_refs=refs,expected_zones=('graveyard',),destination='hand',owned_only=True,
            reason=intent.reason,transition_kind=ZoneTransitionKind.ORDINARY,replacement_selections=intent.replacement_selections))
    return resolve_random_graveyard_return(sink,intent,commit_batch=commit)


def _execute_random_exile_cast(host, intent):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION
    from ..replacement.immutable import FrozenMap
    from ..rules.committed_quantity_followups import CommittedQuantityFollowup
    from ..rules.library_selection import LibrarySelectionObjectIdentity

    def commit(refs):
        ref, = refs
        selected = host._resolve_object(intent.actor, ref, zones={"graveyard"}, owned_only=True)
        before = selected.zone_change_counter
        host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=intent.actor, object_refs=refs, expected_zones=("graveyard",), destination="exile",
            owned_only=True, reason=intent.reason, replacement_selections=intent.replacement_selections,
        ))
        after = host.state.cards.get(selected.object_id)
        if (after is None or after.zone != "exile" or after.face_down or not after.is_card_object
                or after.owner != intent.actor or after.zone_change_counter != before + 1):
            return ()
        identity = LibrarySelectionObjectIdentity(after.object_id, after.logical_object_id, after.ref)
        return (FrozenMap({"op": FIXED_TOP_EXILE_OFFER_OPERATION,
                           "instruction": random_exile_cast_instruction(intent.actor, intent.selection),
                           "results": [{"owner": after.owner, "identity": identity.to_dict()}]}),)

    return CommittedQuantityFollowup(resolve_random_graveyard_return(host, intent, commit_batch=commit))
