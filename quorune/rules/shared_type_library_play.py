"""Authorize a matching revealed library card through ordinary casting and land play."""

from .casting.face_queries import offered_cast_methods
from .casting.proposal import build_cast_offer
from .shared_type_library import materialize_shared_type_source, shared_type_library_payload_dependencies
from .shared_type_library_model import SHARED_TYPE_PLAY_CAPABILITY
from .shared_type_library_play_model import (
    ResolvingSharedTypeLibraryPlayIntent, revealed_shared_type_card, unissued_shared_type_play,
)
from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError, StateInvariantError
from ..replacement.immutable import thaw_value


def _revealed_card(host, item, actor, effect):
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or not item.context.get("currently_resolving") or actor not in host.active_seats):
        raise GameRuleError("Playing the revealed card requires its original resolving trigger")
    try:
        identity, comparison = revealed_shared_type_card(effect, actor=actor)
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not program.effects or SHARED_TYPE_PLAY_CAPABILITY not in program.capability_dependencies):
            raise ValueError("The original Kinship play authority is unavailable")
        original = program.effects[0]
        if (SHARED_TYPE_PLAY_CAPABILITY not in shared_type_library_payload_dependencies(original)
                or thaw_value(original["result"]) != thaw_value(effect["result"])
                or materialize_shared_type_source(host, item) != comparison.source):
            raise ValueError("The revealed card lost its original Kinship instruction")
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    card = host.state.cards.get(identity.object_id)
    if (card is None or card.zone != "library" or card.owner != actor or not card.is_card_object
            or card.face_down or card.ref != identity.ref or card.logical_object_id != identity.logical_object_id
            or host.state.players[actor].zones["library"][-1:] != [card.object_id]
            or not set(host.seats) <= set(card.revealed_to)):
        raise GameRuleError("The original publicly revealed library card changed")
    return card


def _option_pairs(host, item, actor, effect):
    from .land_play_permissions import resolving_land_play_options
    card = _revealed_card(host, item, actor, effect)
    pairs = []
    for option in resolving_land_play_options(host, actor, card, origin="library"):
        pairs.append((option, option, card))
    for method, face in offered_cast_methods(host, card):
        if method is not None:
            continue
        cast = build_cast_offer(host, actor, card, face_name=face,
            resolving_payment="free", resolving_origin="library")
        if cast.offer is None:
            continue
        offer = cast.offer.to_dict(include_fingerprint=False)
        for price in offer["cost_options"]:
            canonical = {**price, "face": offer.get("face"), "cast_method": method}
            identity = discover_options_fingerprint(({
                "logical_object_id": card.logical_object_id, "price": canonical,
            },))
            public = {**canonical, "id": "revealed-library-cast:" + identity, "card": card.ref,
                "label": f"{offer['label']} — {price.get('label', 'Cast without paying its mana cost')}"}
            pairs.append((public, canonical, card))
    return tuple(pairs)


def shared_type_library_play_options(host, *, item, actor, effect):
    if "_stack_ref" in effect and (item is None or effect["_stack_ref"] != item.ref):
        raise GameRuleError("The revealed library play lost its originating stack reference")
    return tuple(public for public, _, _ in _option_pairs(
        host, item, actor, unissued_shared_type_play(effect),
    ))


def _live_selection(host, intent):
    item = next((entry for entry in host.state.stack if entry.ref == intent.stack_ref), None)
    pairs = _option_pairs(host, item, intent.actor, intent.effect)
    if discover_options_fingerprint(tuple(public for public, _, _ in pairs)) != intent.options_fingerprint:
        raise GameRuleError("The revealed library play options changed")
    for public, current, card in pairs:
        if public["id"] == intent.response["cost_option"]:
            if (intent.response.get("card", card.ref) != card.ref
                    or intent.response.get("from", "library") != "library"
                    or intent.response.get("face") not in (None, current.get("face"))
                    or intent.response.get("cast_method") not in (None, current.get("cast_method"))):
                raise GameRuleError("The response changed the authorized library card or face")
            return current, card
    raise GameRuleError("Choose an issued revealed library play")


def resolving_shared_type_land_response(host, actor, intent):
    if not isinstance(intent, ResolvingSharedTypeLibraryPlayIntent) or actor != intent.actor:
        raise GameRuleError("Revealed library land play lost its typed authorization")
    option, card = _live_selection(host, intent)
    response = intent.response
    if (option.get("kind") != "land"
            or set(response) - {"cost_option", "card", "from", "face", "targets", "modes", "auto_pay", "pay"}
            or response.get("targets") not in (None, (), []) or response.get("modes") not in (None, (), [])
            or response.get("auto_pay") is not None and response["auto_pay"] is not True
            or response.get("pay") not in (None, "auto")):
        raise GameRuleError("Land play accepts the sealed entry option")
    return {"card": card.ref, "from": "library", "face": option["face"], "pay_life": option["pay_life"],
        "_entry_replacement_selections": [thaw_value(v) for v in intent.replacement_selections]}


def execute_shared_type_library_play(host, intent):
    from .resolving_cast_payments import commit_resolving_cast
    option, card = _live_selection(host, intent)
    if option.get("kind") == "land":
        host._play_land(intent.actor, {}, resolving=intent)
        return card.ref
    response = {**thaw_value(intent.response), "card": card.ref, "from": "library",
                "cost_option": option["id"], "auto_pay": True}
    if option["face"] is not None:
        response["face"] = option["face"]
    before = card.logical_object_id
    commit_resolving_cast(host, actor=intent.actor, response=response, origin="library",
        payment_id=f"{intent.stack_ref}:kinship:{before}",
        replacement_selections=intent.replacement_selections)
    current = host.state.cards[card.object_id]
    if (current.zone != "stack" or current.logical_object_id == before or current.controller != intent.actor
            or not any(item.card_object_id == current.object_id and item.kind == "spell" for item in host.state.stack)):
        raise StateInvariantError("Playing the revealed library card created no actual spell")
    return current.ref
