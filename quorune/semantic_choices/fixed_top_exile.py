"""Capture and consume a finite set of library-top exile results."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..discover_model import DiscoverCastIntent, ExileCastProgress, discover_options_fingerprint, discover_zone_counter
from ..fixed_top_exile import (
    CHOSEN_IMPULSE_GRANT_FIELD, CHOSEN_IMPULSE_SCOPES, OPPONENT_CHOSEN_IMPULSE_SCOPE, DAMAGE_WINDOW_CAST_SCOPE,
    FIXED_TOP_EXILE_CAPABILITY, FIXED_TOP_EXILE_OPERATION, FIXED_TOP_EXILE_OFFER_OPERATION,
    ANNOUNCED_OPPONENT_FREE_PLAY_SCOPE,
    FIXED_TOP_EXILE_RULES, RESOLVING_TOP_PLAY_SCOPE, RESOLVING_TOP_PLAY_SCOPES, TOTAL_MANA_EXILE_SCOPE,
    ResolvingExileLandPlayIntent,
    fixed_top_exile_result_rows, validate_fixed_top_exile,
)
from ..land_exile_source_return_model import LAND_EXILE_RETURN_SCOPE
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.elected_nonland_exile import (
    ELECTED_NONLAND_EXILE_SCOPE, ELECTION_FIELD, SCAN_FIELD, is_nonland_election_offer,
    nonland_election_offer, validate_nonland_election_offer,
)
from .elected_nonland_exile import (
    complete_nonland_election, prepare_nonland_election, prepare_nonland_exile_scan,
)
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..targeted_exile_land_entry import (
    EXILE_LAND_ENTRY_PAYMENT_FIELD, TARGETED_EXILE_LAND_ENTRY_SCOPE, uses_targeted_exile_land_entry,
)
from .targeted_exile_land_entry import complete_targeted_exile_land_entry, prepare_targeted_exile_land_entry
from .land_exile_source_return import complete_land_exile_source_return, prepare_land_exile_source_return
from .model import (AutoContinue, CastOrDeclineChoice, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)


def _offer(instruction, results):
    return FrozenMap({"op": FIXED_TOP_EXILE_OFFER_OPERATION, "instruction": instruction, "results": results})


def _decline(effect):
    damage = effect["instruction"].get("decline")
    return (FrozenMap(damage),) if damage is not None else ()


def _capture_library_tops(query, owner, count):
    for ref in query.library_refs(owner, top_first=True)[:count]:
        card = query.object(ref, zones=("library",))
        if card is None or card.owner != owner or card.token:
            raise SemanticChoiceError("Fixed top exile requires owned library tops")
        before = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
        yield LibrarySelectionObjectIdentity(card.object_id,
            f"{card.object_id}@{discover_zone_counter(before) + 1}", card.ref)


def _prepare_exile(effect, context):
    if effect.get("scope") == ELECTED_NONLAND_EXILE_SCOPE:
        return prepare_nonland_exile_scan(effect, context)
    from ..mana_value_exile_model import TOTAL_MANA_EXILE_SCOPE
    if effect.get('scope') == TOTAL_MANA_EXILE_SCOPE:
        from .mana_value_exile import prepare_total_mana_exile
        return prepare_total_mana_exile(effect, context)
    scope = validate_fixed_top_exile(effect, resolved=True)
    if scope == DAMAGE_WINDOW_CAST_SCOPE:
        raise SemanticChoiceError("A damage-window cast requires the actual completed private partition")
    if effect["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("Fixed top exile requires the resolving controller")
    if scope == "each_library":
        owners = context.query.active_seats
    elif scope in {"cast_event_library", LAND_EXILE_RETURN_SCOPE, TARGETED_EXILE_LAND_ENTRY_SCOPE,
                   ANNOUNCED_OPPONENT_FREE_PLAY_SCOPE, OPPONENT_CHOSEN_IMPULSE_SCOPE}:
        owners = (effect["supplier"],)
    else:
        owners = (context.actor,)
    count = effect.get("count", 1)
    captured = tuple((owner, identity) for owner in owners if owner in context.query.active_seats
                     for identity in _capture_library_tops(context.query, owner, count))
    refs = tuple(identity.ref for _, identity in captured)
    results = ([] if scope == TARGETED_EXILE_LAND_ENTRY_SCOPE else
               [{"owner": owner, "identity": identity.to_dict()} for owner, identity in captured])
    intents = ((MoveObjectsSimultaneouslyIntent(context.actor, tuple(refs), ("library",), "exile",
        "Exile the fixed library tops"),) if refs else ())
    return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
        auto_continue=AutoContinue(reason="Exile the captured library tops together.",
            prepend_effects=(_offer(effect, results),)))


def _chosen_impulse_candidates(rows, query):
    candidates = []
    for owner, identity in rows:
        card = query.object(identity.ref, zones=("exile",))
        if (card is not None and card.owner == owner and card.object_id == identity.object_id
                and card.logical_object_id == identity.logical_object_id and card.known_to_actor and not card.token):
            candidates.append((identity, card.printed_name))
    return tuple(candidates)


def _chosen_impulse_fingerprint(candidates):
    return discover_options_fingerprint(tuple(identity.to_dict() for identity, _ in candidates))


def _grant_chosen_impulse(effect, identity):
    instruction = effect["instruction"]
    opponent = instruction["scope"] == OPPONENT_CHOSEN_IMPULSE_SCOPE
    grant = {"identity": identity.to_dict(), "duration": instruction["duration"]}
    if opponent:
        grant["supplier"] = instruction["supplier"]
    return FrozenMap({"op": "grant_cast_permission", "schema_version": 2 if opponent else 1,
                      "player": instruction["player"], CHOSEN_IMPULSE_GRANT_FIELD: grant})


def _prepare_chosen_impulse(effect, context, rows):
    candidates = _chosen_impulse_candidates(rows, context.query)
    if len(candidates) <= 1:
        follow = (_grant_chosen_impulse(effect, candidates[0][0]),) if candidates else ()
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="Fewer than two captured exile cards remain.", prepend_effects=follow))
    refs = tuple(identity.ref for identity, _ in candidates)
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt="Choose an exiled card you may play.",
        choice=ObjectChoice(field_name="objects", legal_refs=refs, zones=("exile",),
            owner_relation="any" if effect["instruction"]["scope"] == OPPONENT_CHOSEN_IMPULSE_SCOPE else "you"),
        public_context=FrozenMap({"stack": context.stack_ref, "operation": FIXED_TOP_EXILE_OFFER_OPERATION,
                                 "objects": [{"id": identity.ref, "name": name} for identity, name in candidates]})),
        continuation_effect=FrozenMap({**effect, "_stack_ref": context.stack_ref,
                                       "_options_fingerprint": _chosen_impulse_fingerprint(candidates)}))


def _complete_chosen_impulse(effect, rows, response, query, fingerprint):
    candidates = _chosen_impulse_candidates(rows, query)
    selected = response.get("objects")
    if (not isinstance(selected, (list, tuple)) or len(selected) != 1 or type(selected[0]) is not str
            or _chosen_impulse_fingerprint(candidates) != fingerprint):
        raise SemanticChoiceError("Choose one current captured exile card")
    identity = next((identity for identity, _ in candidates if identity.ref == selected[0]), None)
    if identity is None:
        raise SemanticChoiceError("The chosen card is outside this exile result")
    return SemanticChoiceCompletion(prepend_effects=(_grant_chosen_impulse(effect, identity),))


def _prepare_offer(effect, context):
    if is_nonland_election_offer(effect) and validate_nonland_election_offer(effect)[0] != "cast":
        return prepare_nonland_election(effect, context)
    fields = {"op", "instruction", "results"}
    payment = (EXILE_LAND_ENTRY_PAYMENT_FIELD in effect
               and uses_targeted_exile_land_entry(effect.get("instruction")))
    rows = fixed_top_exile_result_rows({key: effect[key] for key in fields} if payment else effect)
    if payment and set(effect) != fields | {EXILE_LAND_ENTRY_PAYMENT_FIELD}:
        raise SemanticChoiceError("Exile land-entry payment has a closed continuation shape")
    if effect["instruction"]["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("Fixed top exile offer belongs to another controller")
    if effect["instruction"]["scope"] in CHOSEN_IMPULSE_SCOPES:
        return _prepare_chosen_impulse(effect, context, rows)
    if effect["instruction"]["scope"] == LAND_EXILE_RETURN_SCOPE:
        return prepare_land_exile_source_return(effect, context, rows)
    if effect["instruction"]["scope"] == TARGETED_EXILE_LAND_ENTRY_SCOPE:
        return prepare_targeted_exile_land_entry(effect, context)
    options = tuple(context.query.authorized_cast_options())
    play = effect["instruction"]["scope"] in RESOLVING_TOP_PLAY_SCOPES
    if not options:
        reason = "No remaining exiled card can be played." if play else "No remaining exiled card can be cast."
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason=reason, prepend_effects=_decline(effect)))
    selection = "play" if play else "cast"
    prompt = f"Choose an exiled card to {selection} now, or finish resolving."
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt=prompt,
        choice=CastOrDeclineChoice(options, selection=selection),
        public_context=FrozenMap({"stack": context.stack_ref, "operation": FIXED_TOP_EXILE_OFFER_OPERATION,
                                 "cast_options": list(options)})),
        continuation_effect=FrozenMap({**effect, "_stack_ref": context.stack_ref,
                                       "_options_fingerprint": discover_options_fingerprint(options)}))


def _land_play_intent(actor, stack_ref, identity, option, submission, *, supplier=None):
    if (set(submission) - {"cost_option", "face", "cast_method", "targets", "modes", "auto_pay", "pay"}
            or submission.get("face") not in (None, option.get("face"))
            or submission.get("cast_method") is not None or submission.get("targets") not in (None, (), [])
            or submission.get("modes") not in (None, (), [])
            or (submission.get("auto_pay") is not None and submission["auto_pay"] is not True)
            or submission.get("pay") not in (None, "auto")):
        raise SemanticChoiceError("Land play accepts its sealed entry option")
    return ResolvingExileLandPlayIntent(actor, stack_ref, identity, option["id"], supplier=supplier)


def _play_intent(actor, stack_ref, scope, owner, identity, chosen, options, cast):
    if chosen.get("kind") == "land":
        if scope not in RESOLVING_TOP_PLAY_SCOPES:
            raise SemanticChoiceError("This resolving choice authorizes spells only")
        supplier = owner if scope == ANNOUNCED_OPPONENT_FREE_PLAY_SCOPE else None
        return _land_play_intent(actor, stack_ref, identity, chosen, cast, supplier=supplier)
    payment = "paid" if scope in {"controller_library", RESOLVING_TOP_PLAY_SCOPE} else "free"
    progress = ExileCastProgress(None, (identity,), identity.ref, owner, payment)
    group = tuple(option for option in options if option["card"] == identity.ref and option.get("kind") != "land")
    return DiscoverCastIntent(actor, stack_ref, progress, discover_options_fingerprint(group),
                              FrozenMap({**cast, "cost_option": chosen["id"]}))


def _complete_offer(continuation, response, query):
    saved = continuation.effect
    election = is_nonland_election_offer(saved)
    if election and "_options_fingerprint" not in saved:
        return complete_nonland_election(continuation, response, query)
    fields = {"op", "instruction", "results"}
    if election:
        fields.add(ELECTION_FIELD)
    payment = (EXILE_LAND_ENTRY_PAYMENT_FIELD in saved
               and uses_targeted_exile_land_entry(saved.get("instruction")))
    allowed = fields | ({EXILE_LAND_ENTRY_PAYMENT_FIELD} if payment else set())
    if (set(saved) != allowed | {"_stack_ref", "_options_fingerprint"}
            or saved["_stack_ref"] != continuation.stack_ref):
        raise SemanticChoiceError("Fixed top exile choice lost its resolving stack item")
    effect = {key: saved[key] for key in allowed}
    rows = fixed_top_exile_result_rows({key: effect[key] for key in fields} if payment else effect)
    actor = effect["instruction"]["player"]
    if actor not in query.active_seats:
        raise SemanticChoiceError("Fixed top exile choice belongs to an inactive player")
    scope = effect["instruction"]["scope"]
    if scope in CHOSEN_IMPULSE_SCOPES:
        return _complete_chosen_impulse(effect, rows, response, query, saved["_options_fingerprint"])
    if scope == LAND_EXILE_RETURN_SCOPE:
        return complete_land_exile_source_return(effect, response, query, saved["_options_fingerprint"])
    if scope == TARGETED_EXILE_LAND_ENTRY_SCOPE:
        return complete_targeted_exile_land_entry(
            effect, response, query, stack_ref=continuation.stack_ref, fingerprint=saved["_options_fingerprint"],
        )
    options = tuple(query.authorized_cast_options())
    if discover_options_fingerprint(options) != saved["_options_fingerprint"]:
        raise SemanticChoiceError("Fixed top exile casting options changed")
    choice = response.get("choice")
    cast = response.get("cast")
    if scope in RESOLVING_TOP_PLAY_SCOPES:
        submitted = {key: value for key, value in response.items()
                     if key not in {"action", "action_id", "cast", "choice", "choice_schema"}}
        if isinstance(cast, Mapping) and submitted.keys() & cast.keys():
            raise SemanticChoiceError("A play option cannot submit the same field twice")
        if cast is None or isinstance(cast, Mapping):
            cast = {**(cast or {}), **submitted} or None
    if choice == "decline" and cast is None:
        return SemanticChoiceCompletion(prepend_effects=_decline(effect))
    selection = "play" if scope in RESOLVING_TOP_PLAY_SCOPES else "cast"
    if choice != selection or (cast is not None and not isinstance(cast, Mapping)):
        raise SemanticChoiceError(f"Choose one authorized {selection} or finish resolving")
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
    intent = _play_intent(actor, continuation.stack_ref, scope, owner, identity, chosen, options, cast)
    remaining = [row for row in effect["results"] if row["identity"]["ref"] != identity.ref]
    if election:
        stage, opponent, slots, _ = validate_nonland_election_offer(effect)
        if stage != "cast":
            raise SemanticChoiceError("Only an authorized nonland cast consumes a cast slot")
        next_offer = nonland_election_offer(
            effect["instruction"], tuple((owner, other) for owner, other in rows if other != identity),
            stage="cast", opponent=opponent, remaining=slots - 1,
        ) if remaining and slots > 1 else None
        follow = (FrozenMap(next_offer),) if next_offer else ()
    else:
        follow = (_offer(effect["instruction"], remaining),) if remaining and scope in {
            "each_library", *RESOLVING_TOP_PLAY_SCOPES, TOTAL_MANA_EXILE_SCOPE,
        } else ()
    return SemanticChoiceCompletion(intents=(intent,), prepend_effects=follow)


@dataclass(frozen=True, slots=True)
class FixedTopExileHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = FIXED_TOP_EXILE_RULES
    capability_dependencies: tuple[str, ...] = (FIXED_TOP_EXILE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = (
        "instruction", "results", "progress", "pending", "_stack_ref", "_options_fingerprint",
        EXILE_LAND_ENTRY_PAYMENT_FIELD, ELECTION_FIELD, SCAN_FIELD, "player",
    )
    private_data: tuple[str, ...] = ("unexiled library tops",)
    projected_fields: tuple[str, ...] = (
        "prompt", "cast_options", "objects", "legal_actions.choice_schema.legal_refs",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = ("MoveObjectsSimultaneouslyIntent", "ZoneMoveIntent", "DiscoverCastIntent")
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
