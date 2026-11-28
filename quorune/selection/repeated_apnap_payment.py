"""Collect each opponent's public or private choice before committing a common round."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement import ReplacementChoiceRequired
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.repeated_apnap_payment import (
    FIELD, FIXED_DISCARD_VERSION, payment_round_descriptor, payment_round_program_dependencies,
)
from .model import SelectionContinuation, SelectionContract, SelectionModelError, decode_selection_continuation


OPERATION = "selection.nontarget.repeated_apnap_payment.v1"
_ROUND = "_payment_round"
_CHOICES = "_payment_round_choices"
_REPLACEMENTS = "_replacement_selections"
_SACRIFICE_QUERY = ObjectQuerySpec(zones=("battlefield",), excluded_types=("land",))
_DISCARD_QUERY = ObjectQuerySpec(zones=("hand",))


def _fixed_discard(effect):
    return effect.get(FIELD) == FIXED_DISCARD_VERSION


def _count(effect, item):
    return effect["fixed_rounds"] if _fixed_discard(effect) else item.x_value


def require_resolving_payment_item(host, resume, effect):
    from .repeated_payment_physical_order import FIELD as PHYSICAL_ORDER

    item = host.state.stack[-1] if host.state.stack else None
    if item is None or item.ref != resume.get("stack_ref"):
        raise GameRuleError("Repeated payment requires the original stack object")
    host._validate_semantic_frame(resume["semantic_frame"], item)
    program = host.semantics.get(item.semantic_key)
    expected = {key: value for key, value in effect.items() if key not in {_ROUND, _CHOICES, _REPLACEMENTS, PHYSICAL_ORDER}}
    try:
        payment_round_descriptor(expected)
        fixed = _fixed_discard(expected)
        kinds = {"activated_ability"} if fixed else {"spell", "spell_copy"}
        if (item.kind not in kinds or item.context.get("currently_resolving") is not True
                or (not fixed and (type(item.x_value) is not int or item.x_value < 0))
                or program is None or not host.semantic_program_is_current_trusted(program)
                or not payment_round_program_dependencies(program)
                or FrozenMap(expected) != FrozenMap(program.effects[0])):
            raise ValueError("Repeated payment changed its original resolving instruction or choice domain")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return item


def _round(effect, item):
    index = effect.get(_ROUND, 0)
    if type(index) is not int or not 0 <= index < _count(effect, item):
        raise GameRuleError("Repeated payment round is outside its committed count")
    return index


def _continue(host, resume, effects, *, advance=False):
    host._continue_resolution(
        stack_ref=resume["stack_ref"], effects=list(effects), destination=resume["destination"],
        note=resume["note"], instruction_pointer=resume["semantic_frame"]["instruction_pointer"] + int(advance),
    )


def _options(host, seat, *, discard_only=False):
    sacrifices = [] if discard_only else host._choice_options(
        seat, {"zone": "battlefield", "then": "sacrifice", "predicate": _SACRIFICE_QUERY.to_dict()},
    )
    discards = host._choice_options(seat, {"zone": "hand", "then": "discard",
                                           "predicate": _DISCARD_QUERY.to_dict()})
    return {"sacrifice": sacrifices, "discard": discards}


def _order(host, item):
    return [seat for seat in host.apnap_order() if seat != item.controller]


def _check_prefix(host, item, effect, selected, *, complete):
    order = _order(host, item)
    if not isinstance(selected, (list, tuple)) or (complete and len(selected) != len(order)):
        raise GameRuleError("Repeated payment is missing one or more opponents")
    if any(not isinstance(row, Mapping) or set(row) != {"seat", "mode", "ref", "identity"} for row in selected):
        raise GameRuleError("Repeated payment has a malformed choice")
    if _fixed_discard(effect) and any(row["mode"] not in ("discard", "life") for row in selected):
        raise GameRuleError("This loyalty result permits only a discard or life loss")
    if [row["seat"] for row in selected] != order[:len(selected)]:
        raise GameRuleError("Repeated payment choices changed their APNAP order")
    return order


def _validate_choice(host, row):
    seat, mode, ref, identity = (row[key] for key in ("seat", "mode", "ref", "identity"))
    if mode == "life":
        if ref is not None or identity is not None:
            raise GameRuleError("Life loss cannot retain a chosen card")
        return None
    zone = {"sacrifice": "battlefield", "discard": "hand"}.get(mode)
    if zone is None or type(ref) is not str or ref not in _options(host, seat)[mode]:
        raise GameRuleError("That card cannot pay the repeated choice")
    card = host._resolve_object(seat, ref, zones={zone})
    if card.logical_object_id != identity:
        raise GameRuleError("The card chosen in this round has changed")
    return card


def _issue(host, item, effect, resume, selected):
    order = _check_prefix(host, item, effect, selected, complete=False)
    if len(selected) == len(order):
        commit = {**effect, _CHOICES: list(selected)}
        _continue(host, resume, (commit, *resume["effects"]))
        return
    seat = order[len(selected)]
    discard_only = _fixed_discard(effect)
    options = _options(host, seat, discard_only=discard_only)
    refs = (*options["sacrifice"], *options["discard"])
    if discard_only and not refs:
        _issue(host, item, effect, resume, (*selected, {"seat": seat, "mode": "life", "ref": None, "identity": None}))
        return
    selection = SelectionContinuation(
        contract=SelectionContract.NONTARGET_CHOICE, operation_id=OPERATION,
        actor=seat, state_revision=host.state.revision, stack_ref=item.ref,
        source_ref=host._stack_source_ref(item), visibility="actor_private",
        payload=FrozenMap({"effect": effect, "resume": resume, "selected": list(selected),
                           "legal_refs": refs}),
    )
    loss = effect["declined_life_loss"]
    host.permissions.issue(
        kind="choice.apnap", role="pilot", actors=[seat], allowed_actions=["choose"],
        payload_by_actor={seat: {
            "prompt": (f"Discard a card or lose {loss} life." if discard_only
                       else f"Sacrifice a nonland permanent, discard a card, or lose {loss} life."),
            "count": 1, "minimum": 0, "options": list(refs), "declined_life_loss": loss,
            "sacrifice_options": options["sacrifice"], "private_discard_options": options["discard"],
            "round": _round(effect, item) + 1, "rounds": _count(effect, item),
            "prior_payment_modes": {row["seat"]: row["mode"] for row in selected},
            "prior_public_choices": {row["seat"]: [row["ref"]] for row in selected if row["mode"] == "sacrifice"},
        }},
        continuation={"selection": selection.to_dict()},
    )


def complete_payment_round_choice(host, decision):
    try:
        selection = decode_selection_continuation(
            decision.continuation, expected_contract=SelectionContract.NONTARGET_CHOICE,
            expected_operation_id=OPERATION,
        )
    except SelectionModelError as exc:
        raise GameRuleError(str(exc)) from exc
    payload = thaw_value(selection.payload)
    seat, = decision.actors
    effect, resume, selected = (payload[key] for key in ("effect", "resume", "selected"))
    item = require_resolving_payment_item(host, resume, effect)
    order = _check_prefix(host, item, effect, selected, complete=False)
    options = _options(host, seat, discard_only=_fixed_discard(effect))
    refs = (*options["sacrifice"], *options["discard"])
    if (selection.actor != seat or selection.state_revision != decision.created_revision
            or selection.stack_ref != item.ref or selection.source_ref != host._stack_source_ref(item)
            or selection.visibility != "actor_private" or len(selected) >= len(order)
            or seat != order[len(selected)] or tuple(payload["legal_refs"]) != refs or _CHOICES in effect):
        raise GameRuleError("Repeated payment offer changed before completion")
    cards = decision.responses[seat].get("cards", [])
    if (not isinstance(cards, (list, tuple)) or len(cards) > 1
            or any(type(ref) is not str or ref not in refs for ref in cards)):
        raise GameRuleError("Choose one eligible card or no card to lose life")
    if not cards:
        chosen = {"seat": seat, "mode": "life", "ref": None, "identity": None}
    else:
        ref, = cards
        mode = "sacrifice" if ref in options["sacrifice"] else "discard"
        zone = "battlefield" if mode == "sacrifice" else "hand"
        card = host._resolve_object(seat, ref, zones={zone})
        chosen = {"seat": seat, "mode": mode, "ref": ref, "identity": card.logical_object_id}
    _issue(host, item, effect, resume, (*selected, chosen))


def _commit(host, item, effect, selected, index, resume):
    from ..discard_effect_origin import DiscardEffectOrigin
    from ..effect_runtime.life_effects import prepare_life_effect_changes
    from ..life_state import LifeChange
    from ..zone_trigger_events import ZoneTransitionKind
    from ..zone_transitions import ZoneTransitionOwner

    moves = []
    kinds = {}
    losses = []
    for row in selected:
        if (card := _validate_choice(host, row)) is None:
            losses.append(LifeChange(row["seat"], -effect["declined_life_loss"]))
        else:
            moves.append((card.object_id, "graveyard"))
            kinds[card.object_id] = (ZoneTransitionKind.SACRIFICE if row["mode"] == "sacrifice"
                                     else ZoneTransitionKind.DISCARD)
    prefix = f"life.repeated-payment:{item.ref}:{index}"
    journals = effect.get(_REPLACEMENTS, ())
    if any(not isinstance(value, Mapping) or type(value.get("event_id")) is not str for value in journals):
        raise GameRuleError("Repeated payment replacements must name the original event")
    life_choices = [row for row in journals if row["event_id"].startswith(prefix + ":")]
    zone_choices = [row for row in journals if row not in life_choices]
    reason = f"{item.label} payment round"
    life = prepare_life_effect_changes(
        host, losses, actor=item.controller, reason=reason, event_prefix=prefix,
        effect={"source": host._stack_source_ref(item), _REPLACEMENTS: life_choices},
    ) if losses else None
    discard_origin = None
    if ZoneTransitionKind.DISCARD in kinds.values() and host.state.discard_effect_origin_version is not None:
        discard_origin = DiscardEffectOrigin.from_resolving_stack(item)
    owner = ZoneTransitionOwner(host)
    if any(host.state.cards[oid].permanent_composition is not None for oid, _ in moves):
        from .repeated_payment_physical_order import commit_or_offer_payment_physical_order

        if not commit_or_offer_payment_physical_order(
            owner, item=item, changes=moves, kinds=kinds, origin=discard_origin, selections=zone_choices,
            reason=reason, life=life, effect=effect, resume=resume,
        ):
            return False
    else:
        owner.move_cards_simultaneously(
            moves, reason=reason, transition_kinds=kinds, replacement_selections=zone_choices,
            discard_effect_origin=discard_origin, simultaneous_life=life,
        )
    host._log(item.controller, "choice.apnap.payment_round", "The opponents' payment choices resolved.",
              {"round": index + 1, "modes": {row["seat"]: row["mode"] for row in selected}},
              importance=2, changed_players=[row["seat"] for row in selected])
    return True


def begin_payment_round(host, effect, resume):
    item = require_resolving_payment_item(host, resume, effect)
    count = _count(effect, item)
    if not count or not _order(host, item):
        _continue(host, resume, resume["effects"], advance=True)
        return
    index = _round(effect, item)
    if _CHOICES not in effect:
        _issue(host, item, effect, resume, ())
        return
    selected = effect[_CHOICES]
    _check_prefix(host, item, effect, selected, complete=True)
    try:
        if not _commit(host, item, effect, selected, index, resume):
            return
    except ReplacementChoiceRequired as required:
        from ..replacement_decisions import issue_replacement_order_choice

        issue_replacement_order_choice(
            host, item=item, effect=effect, remaining=resume["effects"],
            destination=resume["destination"], note=resume["note"],
            instruction_pointer=resume["semantic_frame"]["instruction_pointer"], required=required,
        )
        return
    if index + 1 < count:
        descriptor = host.semantics.get(item.semantic_key).effects[0]
        begin_payment_round(host, {**descriptor, _ROUND: index + 1}, resume)
        return
    _continue(host, resume, resume["effects"], advance=True)
