"""Collect public choices in turn order before applying either result."""

from dataclasses import dataclass

from ..continuous_effect_model import ContinuousEffectDuration
from ..continuous_effect_state import ResolutionEffectSource
from ..permissions import current_turn_controller
from ..replacement.immutable import FrozenMap
from ..rules.fame_fortune_choices import (
    BASE_FIELDS, FAME_FORTUNE_CAPABILITY, FAME_FORTUNE_OPERATION, validate_fame_fortune,
)
from ..rules.targeted_control_set import TARGETED_CONTROL_SET_RECEIVERS, TARGETED_CONTROL_SET_STEPS
from ..semantic_runtime.control_intents import GainControlSetIntent
from ..semantic_runtime.intents import RecordChoiceIntent
from .model import (AutoContinue, ObjectChoice, ScalarChoice, SemanticChoiceCompletion,
                    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest)

_PROGRESS = frozenset({"_order", "_votes", "_selections"})
_OFFER = "_creature_offer"
_OPTIONS = ("fame", "fortune")


def _base(effect):
    return {key: effect[key] for key in BASE_FIELDS}


def _order_for(query, caster):
    order = tuple(seat for seat in query.apnap_order if seat != caster)
    if set(order) != set(query.active_seats) - {caster} or len(order) != len(set(order)):
        raise SemanticChoiceError("Fame and Fortune did not receive the current turn order")
    return order


def _state(effect, query, caster, *, saved=False):
    fields = set(effect)
    allowed = (BASE_FIELDS | _PROGRESS, BASE_FIELDS | _PROGRESS | {_OFFER})
    if fields not in (allowed if saved else (BASE_FIELDS, allowed[0])):
        raise SemanticChoiceError("Fame and Fortune progress has unexpected fields")
    try:
        validate_fame_fortune(_base(effect), actor=effect["player"], caster=caster)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    current_order = _order_for(query, caster)
    order = effect.get("_order", current_order)
    if (not isinstance(order, (list, tuple)) or any(type(seat) is not str for seat in order)
            or len(set(order)) != len(order) or any(seat == caster or seat not in query.seats for seat in order)
            or tuple(seat for seat in order if seat in query.active_seats) != current_order):
        raise SemanticChoiceError("Fame and Fortune lost the original opponent order")
    votes = effect.get("_votes", ())
    if (not isinstance(votes, (list, tuple)) or any(not isinstance(vote, (list, tuple)) or len(vote) != 2
            or vote[0] not in order or vote[1] not in _OPTIONS for vote in votes)):
        raise SemanticChoiceError("Fame and Fortune has a malformed public choice")
    voters = tuple(seat for seat, _ in votes)
    if tuple(seat for seat in order if seat in voters) != voters:
        raise SemanticChoiceError("Fame and Fortune choices are duplicated or out of turn order")
    next_seat = next((seat for seat in current_order if seat not in voters), None)
    if next_seat is not None and any(order.index(seat) > order.index(next_seat) for seat in voters):
        raise SemanticChoiceError("An earlier opponent has not made their required choice")
    selections = effect.get("_selections", ())
    if not isinstance(selections, (list, tuple)) or next_seat is not None and selections:
        raise SemanticChoiceError("Creatures may only be selected after all opponent choices")
    return tuple(order), tuple(tuple(vote) for vote in votes), tuple(selections), next_seat


def _creatures(query, votes, selections):
    fame = tuple(seat for seat, vote in votes if vote == "fame" and seat in query.active_seats)
    pools = {seat: tuple(row for row in query.objects(zones=("battlefield",), controller=seat)
                        if "creature" in row.types and not row.phased_out) for seat in fame}
    filled = []
    for selection in selections:
        if (not isinstance(selection, (list, tuple)) or len(selection) != 3
                or any(type(field) is not str or not field for field in selection)):
            raise SemanticChoiceError("Each Fame selection needs its controller and current incarnation")
        seat, ref, logical = selection
        if seat in filled or not any((row.ref, row.logical_object_id) == (ref, logical)
                                     for row in pools.get(seat, ())):
            raise SemanticChoiceError("A Fame creature no longer matches its recorded controller and incarnation")
        filled.append(seat)
    next_seat = next((seat for seat in fame if pools[seat] and seat not in filled), None)
    return next_seat, pools.get(next_seat, ())


def _offers(rows):
    return tuple((row.controller, row.ref, row.logical_object_id) for row in rows)


def _progress(effect, order, votes, selections, *, actor):
    return FrozenMap({**_base(effect), "player": actor, "_order": order, "_votes": votes, "_selections": selections})


def _result(effect, context, votes, selections):
    caster = context.stack_controller
    refs = tuple(ref for _, ref, _ in selections)
    control = (GainControlSetIntent(actor=caster, controller=caster, predicate=TARGETED_CONTROL_SET_RECEIVERS,
        duration=ContinuousEffectDuration.UNTIL_END_OF_TURN, source=ResolutionEffectSource(context.stack_ref),
        steps=TARGETED_CONTROL_SET_STEPS, reason=context.stack_label, target_refs=refs),) if refs else ()
    fortune = tuple(item for _, vote in votes if vote == "fortune" for item in (
        FrozenMap({"op": "draw", "player": caster, "count": 1, "private": True}),
        FrozenMap(effect["treasure"]),
    ))
    return SemanticChoicePreparation(
        None, FrozenMap(effect), preparation_intents=control,
        auto_continue=AutoContinue("Apply the remaining Fortune results.", prepend_effects=fortune),
    )


@dataclass(frozen=True, slots=True)
class FameFortuneChoiceHandler:
    operation: str = FAME_FORTUNE_OPERATION
    handler_id: str = "choice.opponents.public-fame-or-fortune.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("101.4", "101.4b", "110.2", "121.2", "608.2c", "611.2c")
    capability_dependencies: tuple[str, ...] = (FAME_FORTUNE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "treasure", "_order", "_votes",
                                            "_selections", _OFFER)
    private_data: tuple[str, ...] = ("The ordinary private identities of cards drawn for Fortune",)
    projected_fields: tuple[str, ...] = ("prompt", "choices", "objects", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("RecordChoiceIntent", "GainControlSetIntent", "draw", "create_token")
    replay_fixture: str = "public-opponent-fame-fortune-order"
    test_modules: tuple[str, ...] = ("tests.test_seize_the_spotlight",)

    def prepare(self, effect, context):
        caster = context.stack_controller
        order, votes, selections, next_seat = _state(effect, context.query, caster)
        actor = next_seat or caster
        if effect["player"] != context.actor or caster not in context.query.active_seats:
            raise SemanticChoiceError("Fame and Fortune lost its resolving controller or actor")
        next_effect = _progress(effect, order, votes, selections, actor=actor)
        if context.actor != actor:
            if set(effect) != BASE_FIELDS and context.actor != caster:
                raise SemanticChoiceError("The next opponent must make their own choice")
            return SemanticChoicePreparation(None, FrozenMap(effect),
                auto_continue=AutoContinue("Continue with the next decision.", prepend_effects=(next_effect,)))
        prior = [{"player": seat, "choice": choice} for seat, choice in votes]
        public = {"stack": context.stack_ref, "choices": prior, "operation": self.operation}
        if next_seat is not None:
            return SemanticChoicePreparation(
                SemanticChoiceRequest("Choose fame or fortune.", ScalarChoice("choice", _OPTIONS), FrozenMap(public)),
                next_effect,
            )
        focus, rows = _creatures(context.query, votes, selections)
        if not rows:
            return _result(effect, context, votes, selections)
        return SemanticChoicePreparation(
            SemanticChoiceRequest("Choose a creature controlled by the player who chose fame.",
                ObjectChoice("objects", tuple(row.ref for row in rows), zones=("battlefield",),
                             controller_relation="opponent"),
                FrozenMap({**public, "player": focus, "objects": [{"id": row.ref, "name": row.printed_name} for row in rows]})),
            FrozenMap({**next_effect, _OFFER: _offers(rows)}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        caster = continuation.semantic_frame.controller
        order, votes, selections, next_seat = _state(effect, query, caster, saved=True)
        if effect["player"] != (next_seat or caster):
            raise SemanticChoiceError("The Fame and Fortune decision belongs to another player")
        if next_seat is not None:
            picked = response.get("choice")
            if picked not in _OPTIONS or _OFFER in effect or "objects" in response:
                raise SemanticChoiceError("Choose either fame or fortune")
            votes = (*votes, (next_seat, picked))
            remaining = next((seat for seat in order if seat in query.active_seats
                              and seat not in {voter for voter, _ in votes}), caster)
            event = RecordChoiceIntent(next_seat, "fame_fortune.choose",
                f"{next_seat} chose {picked}.", FrozenMap({"player": next_seat, "choice": picked}))
            return SemanticChoiceCompletion(intents=(event,),
                prepend_effects=(_progress(effect, order, votes, selections, actor=remaining),))
        _, rows = _creatures(query, votes, selections)
        picked = response.get("objects", ())
        if ("choice" in response or not isinstance(picked, (list, tuple))
                or len(picked) != 1 or effect.get(_OFFER) != _offers(rows)):
            raise SemanticChoiceError("Choose one creature from the current Fame offer")
        selected = next((row for row in rows if row.ref == picked[0]), None)
        if selected is None:
            raise SemanticChoiceError("The selected creature is outside the current Fame offer")
        selections = (*selections, (selected.controller, selected.ref, selected.logical_object_id))
        return SemanticChoiceCompletion(prepend_effects=(_progress(effect, order, votes, selections, actor=caster),))



def _can_concede_fame_fortune(host, continuation, actor):
    if continuation.handler_id != FameFortuneChoiceHandler().handler_id:
        return False
    player = host.state.players.get(actor)
    effect = continuation.effect
    return bool(
        player is not None and player.in_game and current_turn_controller(host.state, actor) is None
        and actor != continuation.semantic_frame.controller and actor == effect.get("player")
        and actor in effect["_order"] and actor not in {seat for seat, _ in effect["_votes"]}
    )


def fame_fortune_concession_offer(host, continuation, actor):
    if not _can_concede_fame_fortune(host, continuation, actor):
        return None
    from ..rules.action_catalog import _concede_offer

    return _concede_offer(host, actor)


def concede_during_fame_fortune(host, *, item, continuation, actor, response):
    from ..turn_priority_owner import commit_confirmed_concession
    from .counter_coordination import continue_semantic_completion

    if response.get("action") != "concede" or not _can_concede_fame_fortune(host, continuation, actor):
        raise SemanticChoiceError("Only the current opponent can concede during their Fame or Fortune choice")
    effect = continuation.effect
    caster = continuation.semantic_frame.controller
    order = effect["_order"]
    votes = effect["_votes"]
    commit_confirmed_concession(host, actor, response)
    if host.state.game_over:
        return
    remaining = next((seat for seat in order if seat in host.active_seats and seat not in {voter for voter, _ in votes}), caster)
    following = _progress(effect, order, votes, (), actor=remaining)
    completion = SemanticChoiceCompletion(repeat_effect=following)
    continue_semantic_completion(host, item=item, continuation=continuation, actor=actor, response=response,
                                 completion=completion)
