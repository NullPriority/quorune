"""The controller chooses an exact face-up linked creature as the ability resolves."""

from ..linked_exile_return_model import ExiledObjectIdentity
from ..rules.linked_loyalty_exile_model import (
    LINKED_LOYALTY_CHOICE_OFFERED, linked_loyalty_entry_instruction, linked_loyalty_exile_instruction,
)
from ..replacement.immutable import FrozenMap
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _current_offers(query):
    offers = {}
    for ref in query.choice_candidate_refs():
        row = query.object(ref, zones=("exile",))
        if row is None or row.face_down or not row.known_to_actor or ref in offers:
            raise SemanticChoiceError("Linked loyalty choice lost a public exile candidate")
        try:
            offers[ref] = ExiledObjectIdentity(row.object_id, row.ref, row.logical_object_id).to_dict()
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
    return FrozenMap(offers)


def prepare_linked_loyalty_exile(effect, context):
    from ..rules.linked_creature_settlement import FIELD

    if FIELD in effect:
        return _prepare_creature_settlement(effect, context)
    from ..rules.linked_death_upkeep import FIELD as DEATH_UPKEEP_FIELD

    if DEATH_UPKEEP_FIELD in effect:
        return _prepare_linked_death_upkeep(effect, context)
    if effect != linked_loyalty_exile_instruction("choose") or context.actor != context.stack_controller:
        raise SemanticChoiceError("The original ability's controller must choose its linked creature")
    offered = _current_offers(context.query)
    if not offered:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="No linked exiled creature matches the announced mana value."),
        )
    rows = [context.query.object(ref, zones=("exile",)) for ref in offered]
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose a linked exiled creature card to put onto the battlefield.",
            choice=ObjectChoice("card", tuple(offered), ("exile",)),
            public_context=FrozenMap({
                "stack": context.stack_ref,
                "operation": effect["op"],
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows],
            }),
        ),
        continuation_effect=FrozenMap({**effect, LINKED_LOYALTY_CHOICE_OFFERED: offered}),
    )


def complete_linked_loyalty_exile(continuation, response, query):
    from ..rules.linked_creature_settlement import FIELD

    effect = continuation.effect
    if FIELD in effect:
        return _complete_creature_settlement(effect, response, query)
    from ..rules.linked_death_upkeep import FIELD as DEATH_UPKEEP_FIELD

    if DEATH_UPKEEP_FIELD in effect:
        return _complete_linked_death_upkeep(effect, response, query)
    offered = effect.get(LINKED_LOYALTY_CHOICE_OFFERED)
    expected = linked_loyalty_exile_instruction("choose")
    if (
        set(effect) != set(expected) | {LINKED_LOYALTY_CHOICE_OFFERED}
        or any(effect[key] != value for key, value in expected.items())
        or not offered or offered != _current_offers(query)
    ):
        raise SemanticChoiceError("Linked loyalty choice no longer has its original exile options")
    selected = response.get("card")
    if type(selected) is not str or selected not in offered:
        raise SemanticChoiceError("Choose exactly one eligible creature card from linked exile")
    try:
        identity = ExiledObjectIdentity.from_dict(offered[selected])
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(linked_loyalty_entry_instruction(identity)),))



def _prepare_creature_settlement(effect, context):
    from ..rules.linked_creature_settlement import CHOICE_OFFERED, instruction

    pair = effect.get("binding_id")
    if FrozenMap(effect) != FrozenMap(instruction(pair, "choose")) or context.actor != context.stack_controller:
        raise SemanticChoiceError("Linked creature settlement requires its original controller")
    offered = _current_offers(context.query)
    if not offered:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="No linked creature can return to the battlefield.",
                prepend_effects=(FrozenMap(instruction(pair, "return_others")),)))
    rows = [context.query.object(ref, zones=("exile",)) for ref in offered]
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose a linked creature card to return to the battlefield.",
            choice=ObjectChoice("card", tuple(offered), ("exile",)),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "choose_objects",
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows]}),
        ),
        continuation_effect=FrozenMap({**effect, CHOICE_OFFERED: offered}),
    )


def _complete_creature_settlement(effect, response, query):
    from ..rules.linked_creature_settlement import CHOICE_OFFERED, instruction

    pair = effect.get("binding_id")
    offered = effect.get(CHOICE_OFFERED)
    selected = response.get("card")
    if (FrozenMap({key: value for key, value in effect.items() if key != CHOICE_OFFERED})
            != FrozenMap(instruction(pair, "choose")) or not offered or offered != _current_offers(query)
            or type(selected) is not str or selected not in offered):
        raise SemanticChoiceError("Choose one creature from the original linked exiles")
    identity = ExiledObjectIdentity.from_dict(offered[selected])
    return SemanticChoiceCompletion(prepend_effects=tuple(
        FrozenMap(instruction(pair, phase, identity)) for phase in ("enter", "return_others")
    ))


def _prepare_linked_death_upkeep(effect, context):
    from ..rules.linked_death_upkeep import OFFERED, instruction

    pair = effect.get("binding_id")
    if FrozenMap(effect) != FrozenMap(instruction(pair, "choose")) or context.actor != context.stack_controller:
        raise SemanticChoiceError("Linked death upkeep requires its original controller")
    offered = _current_offers(context.query)
    if not offered:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="No linked creature card is available."))
    rows = [context.query.object(ref, zones=("exile",)) for ref in offered]
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="You may choose a linked creature card to return with a finality counter.",
            choice=ObjectChoice("objects", tuple(offered), ("exile",), minimum=0, maximum=1, optional=True),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "choose_objects",
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows]}),
        ),
        continuation_effect=FrozenMap({**effect, OFFERED: offered}),
    )


def _complete_linked_death_upkeep(effect, response, query):
    from ..rules.linked_death_upkeep import OFFERED, instruction

    pair = effect.get("binding_id")
    offered = effect.get(OFFERED)
    selected = response.get("objects")
    if (FrozenMap({key: value for key, value in effect.items() if key != OFFERED})
            != FrozenMap(instruction(pair, "choose")) or not offered or offered != _current_offers(query)
            or not isinstance(selected, (list, tuple)) or len(selected) > 1):
        raise SemanticChoiceError("Choose zero or one creature from the original linked exiles")
    if not selected:
        return SemanticChoiceCompletion()
    if type(selected[0]) is not str or selected[0] not in offered:
        raise SemanticChoiceError("The selected creature is outside the original linked exiles")
    identity = ExiledObjectIdentity.from_dict(offered[selected[0]])
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(instruction(pair, "enter", selected=identity)),))
