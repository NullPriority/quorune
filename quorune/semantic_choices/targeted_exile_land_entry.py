"""Offer owned face-up exiled land cards and commit one battlefield-entry batch."""

from collections.abc import Mapping

from ..discover_model import discover_options_fingerprint
from ..public_zone_moves import PublicBattlefieldController
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..targeted_exile_land_entry import EXILE_LAND_ENTRY_PAYMENT_FIELD, uses_targeted_exile_land_entry
from .model import (
    AutoContinue, ObjectChoice, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _eligible_land(row, owner):
    return (row.zone == "exile" and row.owner == owner and row.physical_card
            and not row.face_down and "land" in row.types and row.known_to_actor)


def _entry_candidates(query, instruction, stack_ref):
    actor, owner = instruction["player"], instruction["supplier"]
    stack = query.stack_object(stack_ref)
    if stack is None or stack.controller != actor or actor not in query.active_seats or stack.targets != (owner,):
        raise SemanticChoiceError("Exile land entry lost its original resolving controller or opponent target")
    return tuple(sorted(
        (row.ref, row.logical_object_id, row.printed_name)
        for row in query.objects(zones=("exile",), owner=owner)
        if _eligible_land(row, owner)
    ))


def _fingerprint(candidates):
    return discover_options_fingerprint(tuple((ref, incarnation) for ref, incarnation, _ in candidates))


def exile_land_entry_life_facts(host, actor, effect, rows):
    instruction = effect.get("instruction")
    progress = effect.get(EXILE_LAND_ENTRY_PAYMENT_FIELD)
    if not uses_targeted_exile_land_entry(instruction) or progress is None:
        return {}
    from ..life_state import available_life_for_payment
    from ..semantic_runtime.zone_replacements import typed_entry_life_payment_amount

    if (not isinstance(progress, Mapping) or not isinstance(refs := progress.get("selected"), (list, tuple))
            or any(type(ref) is not str for ref in refs) or instruction.get("player") != actor):
        raise SemanticChoiceError("Exile land-entry payment lost its original selection or payer")
    costs = {}
    for row in rows:
        if row.ref not in refs or not _eligible_land(row, instruction.get("supplier")):
            continue
        card = host.state.cards[row.object_id]
        record = host.card_record(card)
        if record is not None:
            front = str(record.faces[0].get("name") or "") if record.faces else None
            amount = typed_entry_life_payment_amount(host, record, card=card, prospective_name=front)
            if amount:
                costs[row.ref] = amount
    return {"land_entry_life_options": costs, "land_entry_life_budget": available_life_for_payment(host, actor)}


def _selection_is_valid(selected, legal):
    return (isinstance(selected, (list, tuple))
            and all(type(ref) is str and ref in legal for ref in selected)
            and len(selected) == len(set(selected)))


def _payment_step(progress, candidates, query):
    if not isinstance(progress, Mapping) or set(progress) != {"selected", "paid", "start", "fingerprint"}:
        raise SemanticChoiceError("Exile land-entry payment progress is malformed")
    selected, paid, start = progress["selected"], progress["paid"], progress["start"]
    legal = tuple(ref for ref, _, _ in candidates)
    costs = query.land_entry_life_options
    if (progress["fingerprint"] != _fingerprint(candidates)
            or not _selection_is_valid(selected, legal) or not selected
            or tuple(selected) != tuple(ref for ref in legal if ref in selected)
            or type(start) is not int or not 0 <= start <= len(selected)
            or not _selection_is_valid(paid, selected[:start]) or any(ref not in costs for ref in paid)):
        raise SemanticChoiceError("Exile land-entry payment lost its original public selection")
    remaining = query.land_entry_life_budget - sum(costs[ref] for ref in paid)
    if remaining < 0:
        raise SemanticChoiceError("The resolving controller cannot pay for the selected entries")
    return next(((index, cost) for index in range(start, len(selected))
                 if 0 < (cost := costs.get(selected[index], 0)) <= remaining), None)


def _entry_intent(instruction, selected, paid):
    return MoveObjectsSimultaneouslyIntent(
        actor=instruction["player"], object_refs=tuple(selected), expected_zones=("exile",),
        destination="battlefield", reason="Put chosen exiled lands onto the battlefield.",
        battlefield_controller=PublicBattlefieldController.ACTOR, entry_life_payment_refs=tuple(paid),
    )


def _prepare_payment(effect, context, candidates, progress):
    step = _payment_step(progress, candidates, context.query)
    if step is None:
        intent = _entry_intent(effect["instruction"], progress["selected"], progress["paid"])
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect), preparation_intents=(intent,),
            auto_continue=AutoContinue("Put the chosen exiled land cards onto the battlefield together."),
        )
    index, amount = step
    ref = progress["selected"][index]
    name = next(name for key, _, name in candidates if key == ref)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Pay {amount} life as {name} enters?",
            choice=ScalarChoice("pay", (True, False)),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"], "card": ref}),
        ),
        continuation_effect=FrozenMap({
            **effect, "_stack_ref": context.stack_ref,
            "_options_fingerprint": discover_options_fingerprint((ref, amount)),
        }),
    )


def prepare_targeted_exile_land_entry(effect, context):
    candidates = _entry_candidates(context.query, effect["instruction"], context.stack_ref)
    if EXILE_LAND_ENTRY_PAYMENT_FIELD in effect:
        return _prepare_payment(effect, context, candidates, effect[EXILE_LAND_ENTRY_PAYMENT_FIELD])
    if not candidates:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The targeted opponent owns no face-up land cards in exile."))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose any number of the targeted opponent's exiled land cards to put onto the battlefield.",
            choice=ObjectChoice(field_name="objects", legal_refs=tuple(ref for ref, _, _ in candidates),
                                zones=("exile",), minimum=0, maximum=len(candidates), optional=True),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"],
                "objects": [{"id": ref, "name": name} for ref, _, name in candidates],
            }),
        ),
        continuation_effect=FrozenMap({
            **effect, "_stack_ref": context.stack_ref, "_options_fingerprint": _fingerprint(candidates),
        }),
    )


def _complete_payment(effect, response, query, candidates, fingerprint):
    progress = effect[EXILE_LAND_ENTRY_PAYMENT_FIELD]
    step = _payment_step(progress, candidates, query)
    pay = response.get("pay")
    if step is None or type(pay) is not bool:
        raise SemanticChoiceError("Select whether to pay life for the current land entry")
    index, amount = step
    ref = progress["selected"][index]
    if discover_options_fingerprint((ref, amount)) != fingerprint:
        raise SemanticChoiceError("The current land-entry life price changed")
    paid = (*progress["paid"], ref) if pay else progress["paid"]
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
        **effect, EXILE_LAND_ENTRY_PAYMENT_FIELD: {**progress, "paid": paid, "start": index + 1},
    }),))


def complete_targeted_exile_land_entry(effect, response, query, *, stack_ref, fingerprint):
    candidates = _entry_candidates(query, effect["instruction"], stack_ref)
    if EXILE_LAND_ENTRY_PAYMENT_FIELD in effect:
        return _complete_payment(effect, response, query, candidates, fingerprint)
    selected = response.get("objects", ())
    legal = tuple(ref for ref, _, _ in candidates)
    if not _selection_is_valid(selected, legal) or _fingerprint(candidates) != fingerprint:
        raise SemanticChoiceError("Exile land entry requires a subset of the original public candidates")
    if not selected:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
        **effect, EXILE_LAND_ENTRY_PAYMENT_FIELD: {
            "selected": tuple(ref for ref in legal if ref in selected), "paid": (), "start": 0,
            "fingerprint": fingerprint,
        },
    }),))
