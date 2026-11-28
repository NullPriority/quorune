from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..public_library_piles_model import (
    PILE_CAPABILITY,
    PILE_FIELDS,
    PILE_NAMES,
    PILE_OPERATION,
    PILE_RULES,
    PUBLIC_SEARCH_SPLIT_CAPTURE,
    PublicLibraryPileSpec,
    PublicLibraryPileState,
    RevealedPileCard,
    PILE_SILVER_VERSION,
    SEARCHED_PILE_VERSIONS,
    SearchedLibraryPileState,
    searched_pile_effect,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.library_partition import partition_refs
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..semantic_runtime.intents import (
    LibrarySelectionIntent, RecordChoiceIntent, RevealLibraryCardsIntent, ShuffleLibraryIntent,
)
from .intent_replacement import serialized_replacement_selections
from .model import (
    AutoContinue,
    LibraryPartitionChoice,
    ObjectChoice,
    OrderingChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_STATE = "_public_library_piles"
_ORDER = "_public_pile_graveyard_order"
_RESPONSE_FIELD = {
    "choose_opponent": "opponent",
    "divide": "piles",
    "choose_pile": "pile",
    "order_graveyard": "graveyard_order",
}


def _spec(effect):
    if set(effect) not in (PILE_FIELDS, PILE_FIELDS | {_STATE}, PILE_FIELDS | {_STATE, _ORDER}):
        raise SemanticChoiceError("Unexpected public library pile instruction fields")
    return PublicLibraryPileSpec.from_effect(
        {key: effect[key] for key in PILE_FIELDS}, resolved=True,
    )


def _capture(query, controller, count):
    result = []
    for ref in query.library_refs(controller, top_first=True)[:count]:
        row = query.object(ref, zones=("library",))
        if row is None or row.owner != controller or row.token:
            raise SemanticChoiceError("A public reveal requires the controller's exact top cards")
        result.append(RevealedPileCard(
            LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref),
            row.printed_name,
        ))
    return tuple(result)


def _effect(effect, spec, state):
    return FrozenMap({**dict(effect), "player": state.actor(spec), _STATE: state.to_dict()})


def _opponents(query, controller):
    return tuple(seat for seat in query.active_seats if seat != controller)


def _bind(effect, spec, query, controller, stack_ref):
    state = PublicLibraryPileState.from_dict(effect[_STATE])
    if (
        state.controller != controller or state.stack_ref != stack_ref
        or controller not in query.active_seats or state.actor(spec) != spec.player
        or spec.player not in query.active_seats
        or state.opponent is not None and state.opponent not in _opponents(query, controller)
        or (state.stage == "order_graveyard") != (_ORDER in effect)
    ):
        raise SemanticChoiceError("The public pile decision has left its original stack frame or player scope")
    if spec.searched_pair:
        from ..rules.searched_library_pairs import searched_pair_pile_state_is_closed

        if not searched_pair_pile_state_is_closed(state):
            raise SemanticChoiceError("A revealed search result requires its two original singleton piles")
        if spec.player == controller:
            for selected in state.looked:
                identity = selected.identity
                row = query.object(identity.ref, zones=("library",))
                if (row is None or row.owner != controller or row.token or row.printed_name != selected.name
                        or (row.object_id, row.logical_object_id) != (identity.object_id, identity.logical_object_id)):
                    raise SemanticChoiceError("The original publicly searched library cards changed")
    elif spec.player == controller and state.looked != _capture(query, controller, spec.look_count):
        raise SemanticChoiceError("The publicly revealed library incarnations changed")
    return state


def _public_cards(state):
    return tuple({"id": row.identity.ref, "name": row.name} for row in state.looked)


def _public_groups(state):
    if state.groups is None:
        return {}
    return {"first": state.groups.top_top_first, "second": state.groups.destination_refs}


def _retained_order(effect, state):
    retained = effect[_ORDER]
    if (
        not isinstance(retained, Mapping)
        or set(retained) != {"orders", "replacement_selections"}
        or not isinstance(retained["replacement_selections"], (list, tuple))
    ):
        raise SemanticChoiceError("The actual graveyard order lost its replacement choices")
    orders = GraveyardArrivalOrders.from_dict(retained["orders"])
    group = orders.current_group
    if (
        group is None or len(orders.groups) != 1 or orders.choices
        or group.owner != state.controller or not set(group.refs) <= set(state.graveyard_refs())
    ):
        raise SemanticChoiceError("The controller must order only actual graveyard pile arrivals")
    identities = {row.identity.ref: row.identity for row in state.looked}
    for ref, identity in group.members:
        revealed = identities[ref]
        if (revealed.object_id, revealed.logical_object_id) != (identity.object_id, identity.logical_object_id):
            raise SemanticChoiceError("The prepared graveyard arrivals changed their revealed identities")
    return orders, tuple(thaw_value(retained["replacement_selections"]))


def _request(state, query, orders=None, *, spec=None):
    context = {"stack": state.stack_ref, "operation": PILE_OPERATION, "stage": state.stage,
               "objects": _public_cards(state)}
    if state.groups is not None:
        context["piles"] = _public_groups(state)
    if state.stage == "choose_opponent":
        prompt = "Choose an opponent for the revealed library cards." if spec and spec.silver_split else "Choose an opponent for the revealed library piles."
        choice = ScalarChoice("opponent", _opponents(query, state.controller))
    elif state.stage == "divide":
        prompt = "Divide every revealed card between the two piles. Either pile may be empty."
        choice = LibraryPartitionChoice(
            field_name="piles", legal_refs=state.refs, visibility="public",
            partitions=FrozenMap({
                "first": {"label": "First pile", "order": "selection_order"},
                "second": {"label": "Second pile", "order": "selection_order"},
            }), legacy_destination=None,
        )
    elif state.stage == "choose_pile" and spec and spec.silver_split:
        from .model import ObjectChoice

        prompt = "Choose one revealed card to put into its owner's hand."
        choice = ObjectChoice(field_name="objects", legal_refs=state.refs, zones=("library",))
    elif state.stage == "choose_pile":
        names = {row.identity.ref: row.name for row in state.looked}
        first, second = (
            ", ".join(names[ref] for ref in _public_groups(state)[group]) or "empty"
            for group in PILE_NAMES
        )
        prompt = (f"Choose one revealed card to put into hand. First card — {first}. Second card — {second}."
                  if spec and spec.searched_pair else
                  f"Choose a pile to put into hand. First pile — {first}. Second pile — {second}.")
        choice = ScalarChoice("pile", PILE_NAMES)
    elif state.stage == "order_graveyard" and orders is not None and orders.current_group is not None:
        prompt = "Order the cards entering your graveyard from top to bottom."
        choice = OrderingChoice(
            field_name="graveyard_order", legal_refs=orders.current_group.refs,
            schema_extras=FrozenMap({"order": "graveyard_top_to_bottom"}),
        )
    else:
        raise SemanticChoiceError("A public pile order requires the actual prepared graveyard arrivals")
    return SemanticChoiceRequest(prompt, choice, FrozenMap(context))


def _commit(state, spec=None):
    marked = bool(spec and spec.silver_split)
    searched_pair = bool(spec and spec.searched_pair)
    return LibrarySelectionIntent(
        actor=state.controller, player=state.controller,
        arrangement=LibrarySelectionArrangement(
            looked=tuple(row.identity for row in state.looked),
            selected_refs=state.hand_refs(), remainder_refs=state.graveyard_refs(),
            remainder_destination="exile" if marked else "graveyard", remainder_order="chosen",
            silver_exile=marked, searched_pair=searched_pair,
        ),
        reason=state.label, source_stack_ref=state.stack_ref,
        looked_are_public=True, selected_are_public=True,
        require_graveyard_order=not (marked or searched_pair),
    )


def _searched_state(effect):
    state = SearchedLibraryPileState.from_dict(effect[_STATE])
    expected = searched_pile_effect(state)
    if state.stage == "order_graveyard":
        expected[_ORDER] = thaw_value(effect[_ORDER])
    if type(effect.get("schema_version")) is not int or thaw_value(effect) != expected:
        raise SemanticChoiceError("Searched public choices require their complete retained payload")
    return state


def _uses_public_search_split_continuation(effect):
    version = effect.get("schema_version")
    return version in SEARCHED_PILE_VERSIONS and (
        version != PILE_SILVER_VERSION or effect.get("opponent_role") != "chooser"
    )


def scoped_searched_pile_refs(host, item, actor, effect):
    if effect.get("op") != PILE_OPERATION or not _uses_public_search_split_continuation(effect):
        return ()
    state = _searched_state(effect)
    if (item is None or item.context.get("currently_resolving") is not True
            or item.ref != state.stack_ref or item.controller != state.controller or actor != state.actor):
        raise SemanticChoiceError("Searched public references require the authorized resolving player")
    if state.stage == "choose_card" and state.target in host.active_seats and (
        actor != state.target or tuple(item.targets) != (state.target,)
    ):
        raise SemanticChoiceError("The current targeted opponent must receive the searched public choice")
    captured = SearchedLibraryPileState.from_dict(item.context[PUBLIC_SEARCH_SPLIT_CAPTURE])
    if (captured.stack_ref != item.ref or captured.controller != state.controller
            or captured.target != state.target or captured.looked != state.looked
            or captured.schema_version != state.schema_version):
        raise SemanticChoiceError("The public choice no longer refers to the captured search")
    if state.stage == "shuffle":
        return ()
    for result in state.looked:
        identity = result.identity
        current = host.state.cards.get(identity.object_id)
        if (current is None or current.owner != state.controller or current.zone != "library"
                or current.ref != identity.ref or current.logical_object_id != identity.logical_object_id
                or actor not in current.revealed_to):
            raise SemanticChoiceError("The searched card is no longer a revealed library incarnation")
    return state.refs


def _bind_searched_state(effect, query, controller, stack_ref):
    state = _searched_state(effect)
    stack = query.stack_object(stack_ref)
    target_present = state.target in query.active_seats
    if (state.controller != controller or controller not in query.active_seats or state.stack_ref != stack_ref
            or stack is None or stack.controller != controller or stack.label != state.label
            or target_present and tuple(stack.targets) != (state.target,)
            or state.stage == "choose_opponent" and target_present
            or state.stage == "choose_card" and (
                state.chooser not in _opponents(query, controller) or target_present and state.chooser != state.target
            )):
        raise SemanticChoiceError("Searched public choices lost their resolving source or chooser")
    if state.stage != "shuffle":
        for card in state.looked:
            identity = card.identity
            row = query.object(identity.ref, zones=("library",))
            if (row is None or row.owner != controller or row.object_id != identity.object_id
                    or row.logical_object_id != identity.logical_object_id or row.printed_name != card.name
                    or not row.known_to_actor):
                raise SemanticChoiceError("The searched library card lost its original public incarnation")
    return state


def _searched_request(state, query, orders=None):
    context = FrozenMap({
        "stack": state.stack_ref, "operation": PILE_OPERATION, "stage": state.stage,
        "objects": _public_cards(state),
    })
    if state.stage == "choose_opponent":
        return SemanticChoiceRequest(
            ("Choose another opponent to select two of the revealed cards." if state.chooses_for_graveyard
             else "Choose another opponent to select one of the revealed cards."),
            ScalarChoice("opponent", _opponents(query, state.controller)), context,
        )
    if state.stage == "choose_card":
        if state.chooses_for_graveyard:
            return SemanticChoiceRequest(
                "Choose two revealed cards to put into the searching player's graveyard.",
                ObjectChoice("cards", state.refs, ("library",), minimum=2, maximum=2), context,
            )
        return SemanticChoiceRequest(
            "Choose one revealed card to put into the searching player's hand.",
            ObjectChoice("cards", state.refs, ("library",)), context,
        )
    if state.stage == "order_graveyard" and orders is not None and orders.current_group is not None:
        return SemanticChoiceRequest(
            "Order the cards entering your graveyard from top to bottom.",
            OrderingChoice(field_name="graveyard_order", legal_refs=orders.current_group.refs,
                           schema_extras=FrozenMap({"order": "graveyard_top_to_bottom"})), context,
        )
    raise SemanticChoiceError("The searched public choice has no valid decision stage")


def _prepare_searched_pile(effect, context):
    state = _bind_searched_state(effect, context.query, context.stack_controller, context.stack_ref)
    if context.actor != state.actor:
        raise SemanticChoiceError("The searched public choice belongs to a different player")
    saved = FrozenMap(effect)
    if state.stage == "commit":
        return SemanticChoicePreparation(
            None, saved, preparation_intents=(_commit(state),),
            auto_continue=AutoContinue(
                reason=("Move the revealed cards to their assigned destinations" if state.chooses_for_graveyard
                        else "Move the chosen card and the remainder"),
                prepend_effects=(FrozenMap(searched_pile_effect(replace(state, stage="shuffle"))),),
            ),
        )
    if state.stage == "shuffle":
        return SemanticChoicePreparation(
            None, saved, preparation_intents=(ShuffleLibraryIntent(state.controller, state.controller, state.label),),
            auto_continue=AutoContinue(reason="Shuffle the searched library"),
        )
    orders = _retained_order(effect, state)[0] if state.stage == "order_graveyard" else None
    return SemanticChoicePreparation(_searched_request(state, context.query, orders), saved)


def _complete_searched_pile(continuation, response, query):
    effect = continuation.effect
    state = _bind_searched_state(effect, query, continuation.semantic_frame.controller, continuation.stack_ref)
    field = {"choose_opponent": "opponent", "choose_card": "cards", "order_graveyard": "graveyard_order"}.get(state.stage)
    if (field is None or set(response) - {"action", "action_id", "choice_schema"} != {field}
            or response.get("action", "choose") != "choose" or response.get("action_id", "choose") != "choose"):
        raise SemanticChoiceError("Provide the currently authorized searched public choice")
    if state.stage == "choose_opponent":
        chooser = response[field]
        if chooser not in _opponents(query, state.controller):
            raise SemanticChoiceError("A replacement chooser must be a current opponent")
        updated = replace(state, chooser=chooser, stage="choose_card")
    elif state.stage == "choose_card":
        selected = partition_refs(response[field], field="cards")
        if state.chooses_for_graveyard:
            if len(selected) != 2 or any(ref not in state.refs for ref in selected):
                raise SemanticChoiceError("Choose exactly two distinct revealed searched cards")
            updated = replace(state, chosen=tuple(selected), stage="commit")
            return SemanticChoiceCompletion(repeat_effect=FrozenMap(searched_pile_effect(updated)))
        if len(selected) != 1 or selected[0] not in state.refs:
            raise SemanticChoiceError("Choose exactly one revealed searched card")
        updated = replace(state, chosen=selected[0], stage="commit")
    else:
        orders, selections = _retained_order(effect, state)
        issued = _searched_request(state, query, orders).choice.choice_schema()
        if "choice_schema" in response and FrozenMap(response["choice_schema"]) != FrozenMap(issued):
            raise SemanticChoiceError("The searched placement order changed its issued schema")
        order = partition_refs(response[field], field="graveyard_order")
        accepted = orders.submit(state.controller, tuple(reversed(order)))
        return SemanticChoiceCompletion(
            intents=(replace(_commit(state), replacement_selections=selections, graveyard_orders=accepted),),
            repeat_effect=FrozenMap(searched_pile_effect(replace(state, stage="shuffle"))),
        )
    return SemanticChoiceCompletion(repeat_effect=FrozenMap(searched_pile_effect(updated)))


def _prepare_searched_graveyard_order(continuation, intent, required, *, index, count):
    effect = continuation.effect
    state = _searched_state(effect)
    if (index != 0 or count != 1 or state.stage != "commit" or _ORDER in effect
            or state.stack_ref != continuation.stack_ref or state.controller != continuation.semantic_frame.controller
            or replace(intent, replacement_selections=()) != _commit(state)):
        raise SemanticChoiceError("The searched order must belong to its original public placement")
    waiting = replace(state, stage="order_graveyard")
    saved = FrozenMap({
        **searched_pile_effect(waiting),
        _ORDER: {"orders": required.orders.to_dict(),
                 "replacement_selections": serialized_replacement_selections(intent.replacement_selections)},
    })
    orders, _ = _retained_order(saved, waiting)
    return state.controller, SemanticChoicePreparation(_searched_request(waiting, None, orders), saved)


def prepare_public_pile_graveyard_order(_host, continuation, intent, required, *, index, count):
    effect = continuation.effect
    if _uses_public_search_split_continuation(effect):
        return _prepare_searched_graveyard_order(continuation, intent, required, index=index, count=count)
    spec = _spec(effect)
    state = PublicLibraryPileState.from_dict(effect[_STATE])
    if (
        index != 0 or count != 1 or state.stage != "commit" or _ORDER in effect
        or state.stack_ref != continuation.stack_ref or state.controller != continuation.semantic_frame.controller
        or spec.player != state.controller
        or replace(intent, replacement_selections=()) != _commit(state)
    ):
        raise SemanticChoiceError("A graveyard order must belong to the resolving public pile movement")
    waiting = replace(state, stage="order_graveyard")
    saved = FrozenMap({
        **_effect(effect, spec, waiting),
        _ORDER: {
            "orders": required.orders.to_dict(),
            "replacement_selections": serialized_replacement_selections(intent.replacement_selections),
        },
    })
    orders, _ = _retained_order(saved, waiting)
    return state.controller, SemanticChoicePreparation(_request(waiting, None, orders), saved)


@dataclass(frozen=True, slots=True)
class PublicLibraryPilesHandler:
    operation: str = PILE_OPERATION
    handler_id: str = "choice.library.public-piles.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = PILE_RULES
    capability_dependencies: tuple[str, ...] = (PILE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "schema_version", "look_count", "opponent_role", _STATE, _ORDER)
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "stage", "objects", "piles", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "RecordChoiceIntent", "LibrarySelectionIntent")
    replay_fixture: str = "public-library-piles"
    test_modules: tuple[str, ...] = ("tests.test_public_library_piles",)

    def prepare(self, effect, context):
        try:
            return self._prepare(effect, context)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def _prepare(self, effect, context):
        if _uses_public_search_split_continuation(effect):
            return _prepare_searched_pile(effect, context)
        spec = _spec(effect)
        if spec.player != context.actor:
            raise SemanticChoiceError("A public pile choice requires its currently authorized actor")
        if _STATE in effect:
            state = _bind(effect, spec, context.query, context.stack_controller, context.stack_ref)
            saved = FrozenMap(effect)
            if state.stage == "commit":
                return SemanticChoicePreparation(
                    None, saved, preparation_intents=(_commit(state, spec),),
                    auto_continue=AutoContinue(reason="Move the exact public piles"),
                )
            orders = _retained_order(effect, state)[0] if state.stage == "order_graveyard" else None
            return SemanticChoicePreparation(_request(state, context.query, orders, spec=spec), saved)

        if spec.searched_pair:
            raise SemanticChoiceError("Searched piles require a completed private search and public reveal")
        if context.actor != context.stack_controller:
            raise SemanticChoiceError("Only the resolving controller can reveal their library for piles")
        looked = _capture(context.query, context.actor, spec.look_count)
        if not looked:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), auto_continue=AutoContinue(reason="No library cards to reveal"),
            )
        opponents = _opponents(context.query, context.actor)
        state = PublicLibraryPileState(
            context.actor, context.stack_ref, context.stack_label, looked,
            "divide" if spec.opponent_role == "chooser" or len(opponents) == 1 else "choose_opponent",
            opponents[0] if spec.opponent_role == "divider" and len(opponents) == 1 else None,
        )
        reveal = RevealLibraryCardsIntent(
            actor=context.actor, player=context.actor, viewer=context.actor,
            refs_top_first=state.refs, reason=context.stack_label, public=True,
        )
        publish = RecordChoiceIntent(
            actor=context.actor, event_code="library.piles.reveal",
            message=f"{context.actor} revealed {', '.join(card.name for card in looked)}.",
            details=FrozenMap({"objects": state.refs, "names": tuple(card.name for card in looked)}),
            changed_object_refs=state.refs, changed_players=(context.actor,),
        )
        prepared = (reveal, publish)
        if not opponents:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), preparation_intents=prepared,
                auto_continue=AutoContinue(reason="No active opponent can make a pile choice"),
            )
        if spec.silver_split and len(looked) == 1:
            hand = LibrarySelectionIntent(actor=context.actor, player=context.actor,
                arrangement=LibrarySelectionArrangement((looked[0].identity,), state.refs, (), "graveyard", "chosen"),
                reason=context.stack_label, source_stack_ref=context.stack_ref,
                looked_are_public=True, selected_are_public=True)
            return SemanticChoicePreparation(None, FrozenMap(effect), preparation_intents=(*prepared, hand),
                auto_continue=AutoContinue(reason="The sole revealed card goes to hand."))
        if spec.silver_split:
            groups = state.split({"first": state.refs[:1], "second": state.refs[1:]})
            state = replace(state, groups=groups,
                stage="choose_pile" if len(opponents) == 1 else "choose_opponent",
                opponent=opponents[0] if len(opponents) == 1 else None)
        saved = _effect(effect, spec, state)
        if state.actor(spec) != context.actor:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), preparation_intents=prepared,
                auto_continue=AutoContinue(reason="The sole opponent chooses a revealed card" if spec.silver_split
                                           else "The sole opponent divides the revealed cards",
                                           prepend_effects=(saved,)),
            )
        return SemanticChoicePreparation(_request(state, context.query, spec=spec), saved, preparation_intents=prepared)

    def complete(self, continuation, response, query):
        try:
            return self._complete(continuation, response, query)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def _complete(self, continuation, response, query):
        effect = continuation.effect
        if _uses_public_search_split_continuation(effect):
            return _complete_searched_pile(continuation, response, query)
        spec = _spec(effect)
        state = _bind(effect, spec, query, continuation.semantic_frame.controller, continuation.stack_ref)
        field = "objects" if spec.silver_split and state.stage == "choose_pile" else _RESPONSE_FIELD[state.stage]
        if (
            set(response) - {"action", "action_id", "choice_schema"} != {field}
            or response.get("action", "choose") != "choose"
            or response.get("action_id", "choose") != "choose"
        ):
            raise SemanticChoiceError("Provide only the currently authorized pile choice")
        value = response[field]
        events = ()
        if state.stage == "choose_opponent":
            if value not in _opponents(query, state.controller):
                raise SemanticChoiceError("Choose a current opponent")
            next_state = replace(state, opponent=value, stage="divide" if spec.opponent_role == "divider" else "choose_pile")
        elif state.stage == "divide":
            groups = state.split(value)
            divided = replace(state, groups=groups)
            events = (RecordChoiceIntent(
                actor=spec.player, event_code="library.piles.divide",
                message=f"{spec.player} divided the publicly revealed cards into two piles.",
                details=FrozenMap(_public_groups(divided)),
            ),)
            if spec.opponent_role == "divider":
                next_state = replace(divided, stage="choose_pile")
            else:
                opponents = _opponents(query, state.controller)
                if not opponents:
                    return SemanticChoiceCompletion(intents=events)
                next_state = replace(
                    divided, stage="choose_pile" if len(opponents) == 1 else "choose_opponent",
                    opponent=opponents[0] if len(opponents) == 1 else None,
                )
        elif state.stage == "choose_pile":
            if spec.silver_split:
                if not isinstance(value, (list, tuple)) or len(value) != 1 or value[0] not in state.refs:
                    raise SemanticChoiceError("Choose exactly one publicly revealed card")
                value = PILE_NAMES[state.refs.index(value[0])]
            if value not in PILE_NAMES:
                raise SemanticChoiceError("Choose the first or second pile")
            next_state = replace(state, chosen=value, stage="commit")
        else:
            orders, selections = _retained_order(effect, state)
            issued = _request(state, query, orders).choice.choice_schema()
            if "choice_schema" in response and FrozenMap(response["choice_schema"]) != FrozenMap(issued):
                raise SemanticChoiceError("The graveyard order response changed its issued schema")
            order = partition_refs(value, field="graveyard_order")
            accepted = orders.submit(state.controller, tuple(reversed(order)))
            return SemanticChoiceCompletion(intents=(replace(
                _commit(state), replacement_selections=selections, graveyard_orders=accepted,
            ),))
        return SemanticChoiceCompletion(intents=events, repeat_effect=_effect(effect, spec, next_state))
