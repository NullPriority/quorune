"""Capture and consume a finite set of library-top exile results."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..discover_model import DiscoverCastIntent, ExileCastProgress, discover_options_fingerprint, discover_zone_counter
from ..fixed_top_exile import (
    FIXED_TOP_EXILE_CAPABILITY, FIXED_TOP_EXILE_OPERATION, FIXED_TOP_EXILE_OFFER_OPERATION,
    FIXED_TOP_EXILE_RULES, fixed_top_exile_result_rows, validate_fixed_top_exile,
)
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .model import (AutoContinue, CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)


def _offer(instruction, results):
    return FrozenMap({"op": FIXED_TOP_EXILE_OFFER_OPERATION, "instruction": instruction, "results": results})


def _decline(effect):
    damage = effect["instruction"]["decline"]
    return (FrozenMap(damage),) if damage is not None else ()


def _prepare_exile(effect, context):
    scope = validate_fixed_top_exile(effect, resolved=True)
    if effect["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("Fixed top exile requires the resolving controller")
    if scope == "each_library":
        owners = context.query.active_seats
    elif scope == "cast_event_library":
        owners = (effect["supplier"],)
    else:
        owners = (context.actor,)
    refs = []
    results = []
    for owner in owners:
        if owner not in context.query.active_seats:
            continue
        tops = context.query.library_refs(owner, top_first=True)
        if not tops:
            continue
        card = context.query.object(tops[0], zones=("library",))
        if card is None or card.owner != owner or card.token:
            raise SemanticChoiceError("Fixed top exile requires an owned library top")
        before = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
        after = LibrarySelectionObjectIdentity(card.object_id,
            f"{card.object_id}@{discover_zone_counter(before) + 1}", card.ref)
        refs.append(card.ref)
        results.append({"owner": owner, "identity": after.to_dict()})
    intents = ((MoveObjectsSimultaneouslyIntent(context.actor, tuple(refs), ("library",), "exile",
        "Exile the fixed library tops"),) if refs else ())
    return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
        auto_continue=AutoContinue(reason="Exile the captured library tops together.",
            prepend_effects=(_offer(effect, results),)))


def _prepare_offer(effect, context):
    fixed_top_exile_result_rows(effect)
    if effect["instruction"]["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("Fixed top exile offer belongs to another controller")
    options = tuple(context.query.authorized_cast_options())
    if not options:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="No remaining exiled card can be cast.", prepend_effects=_decline(effect)))
    prompt = "Choose an exiled card to cast now, or finish resolving."
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt=prompt, choice=CastOrDeclineChoice(options),
        public_context=FrozenMap({"stack": context.stack_ref, "operation": FIXED_TOP_EXILE_OFFER_OPERATION,
                                 "cast_options": list(options)})),
        continuation_effect=FrozenMap({**effect, "_stack_ref": context.stack_ref,
                                       "_options_fingerprint": discover_options_fingerprint(options)}))


def _complete_offer(continuation, response, query):
    saved = continuation.effect
    if (set(saved) != {"op", "instruction", "results", "_stack_ref", "_options_fingerprint"}
            or saved["_stack_ref"] != continuation.stack_ref):
        raise SemanticChoiceError("Fixed top exile choice lost its resolving stack item")
    effect = {key: saved[key] for key in ("op", "instruction", "results")}
    rows = fixed_top_exile_result_rows(effect)
    actor = effect["instruction"]["player"]
    if actor not in query.active_seats:
        raise SemanticChoiceError("Fixed top exile choice belongs to an inactive player")
    options = tuple(query.authorized_cast_options())
    if discover_options_fingerprint(options) != saved["_options_fingerprint"]:
        raise SemanticChoiceError("Fixed top exile casting options changed")
    choice = response.get("choice")
    cast = response.get("cast")
    if choice == "decline" and cast is None:
        return SemanticChoiceCompletion(prepend_effects=_decline(effect))
    if choice != "cast" or (cast is not None and not isinstance(cast, Mapping)):
        raise SemanticChoiceError("Choose one authorized cast or finish resolving")
    cast = dict(cast or {})
    selected = cast.get("cost_option")
    matches = [option for option in options if selected is None or option["id"] == selected]
    if len(matches) != 1:
        raise SemanticChoiceError("Select one current exiled card and cost")
    chosen = matches[0]
    candidates = [(owner, identity) for owner, identity in rows if identity.ref == chosen["card"]]
    if len(candidates) != 1:
        raise SemanticChoiceError("The casting option lost its captured exile incarnation")
    owner, identity = candidates[0]
    scope = effect["instruction"]["scope"]
    payment = "paid" if scope == "controller_library" else "free"
    progress = ExileCastProgress(None, (identity,), identity.ref, owner, payment)
    group = tuple(option for option in options if option["card"] == identity.ref)
    intent = DiscoverCastIntent(actor, continuation.stack_ref, progress, discover_options_fingerprint(group),
                                FrozenMap({**cast, "cost_option": chosen["id"]}))
    remaining = [row for row in effect["results"] if row["identity"]["ref"] != identity.ref]
    follow = (_offer(effect["instruction"], remaining),) if remaining and scope == "each_library" else ()
    return SemanticChoiceCompletion(intents=(intent,), prepend_effects=follow)


@dataclass(frozen=True, slots=True)
class FixedTopExileHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = FIXED_TOP_EXILE_RULES
    capability_dependencies: tuple[str, ...] = (FIXED_TOP_EXILE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("instruction", "results", "_stack_ref", "_options_fingerprint")
    private_data: tuple[str, ...] = ("unexiled library tops",)
    projected_fields: tuple[str, ...] = ("prompt", "cast_options")
    mutation_path: tuple[str, ...] = ("MoveObjectsSimultaneouslyIntent", "DiscoverCastIntent")
    replay_fixture: str = "fixed-library-top-exile-and-resolving-cast"
    test_modules: tuple[str, ...] = ("tests.test_discover_rules",)

    def prepare(self, effect, context):
        if effect.get("op") != self.operation:
            raise SemanticChoiceError("Fixed top exile instruction changed")
        try:
            if self.operation == FIXED_TOP_EXILE_OPERATION:
                return _prepare_exile(effect, context)
            return _prepare_offer(effect, context)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        if self.operation != FIXED_TOP_EXILE_OFFER_OPERATION:
            raise SemanticChoiceError("Capturing library tops accepts no choice")
        try:
            return _complete_offer(continuation, response, query)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc


FIXED_TOP_EXILE_HANDLERS = (
    FixedTopExileHandler(FIXED_TOP_EXILE_OPERATION, "choice.library.fixed-top-exile.v1"),
    FixedTopExileHandler(FIXED_TOP_EXILE_OFFER_OPERATION, "choice.library.fixed-top-exile-cast.v1"),
)
