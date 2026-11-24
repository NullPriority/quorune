"""Validate private variable reveal selections and use stack-cost disclosure."""

from ...colored_hand_reveal_model import VariableColoredRevealPrice
from ...effect_runtime.hand_inspection import begin_stack_cost_hand_reveal
from ...object_predicate import ObjectQuerySpec
from ...object_query import object_query_result, query_objects
from ...replacement.immutable import FrozenMap


def variable_colored_reveal_candidates(host, actor, price):
    if not isinstance(price, VariableColoredRevealPrice) or actor not in host.active_seats:
        raise ValueError("A colored reveal requires its active controller and typed price")
    rows = []
    for oid in host.state.players[actor].zones["hand"]:
        card = host.state.cards[oid]
        effective = host._effective_card_data(card)
        rows.append(object_query_result(
            card, effective, type_parts=host._type_parts(str(effective.get("type_line") or "")),
            known_to_actor=True, attached_to_ref=None,
        ))
    return tuple(query_objects(rows, ObjectQuerySpec(
        zones=("hand",), owner=actor, colors_all=(price.color,), known_to_actor=True,
    )))


def prepare_variable_colored_reveal_payment(host, actor, price, announced_x, refs):
    if (type(announced_x) is not int or announced_x < 0 or not isinstance(refs, (tuple, list))
            or len(refs) != announced_x or any(type(ref) is not str or not ref for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ValueError("Reveal exactly the announced number of distinct current hand cards")
    candidates = {row.ref: row for row in variable_colored_reveal_candidates(host, actor, price)}
    if not set(refs) <= set(candidates):
        raise ValueError("The reveal price selects current cards of the required color")
    return tuple(FrozenMap({
        "object_id": row.object_id, "logical_object_id": row.logical_object_id, "ref": row.ref,
        "player": actor, "oracle_id": host.state.cards[row.object_id].oracle_id, "name": row.printed_name,
    }) for ref in refs for row in (candidates[ref],))


def commit_variable_colored_reveal_payment(host, actor, price, selections, *, item, source_ref):
    if item.kind != "activated_ability" or item.controller != actor or item not in host.state.stack:
        raise ValueError("Reveal payment requires its actual activated ability")
    actual = revalidate_variable_colored_reveal_payment(host, actor, price, item.x_value, selections)
    for row in actual:
        begin_stack_cost_hand_reveal(
            host, row, item=item, source_ref=source_ref,
            summary="A card was revealed to pay an activation cost.",
        )


def variable_colored_reveal_offer(host, actor, price, x_schema):
    refs = [row.ref for row in variable_colored_reveal_candidates(host, actor, price)]
    return {
        "x": {**x_schema, "maximum": len(refs)},
        "reveal_cards": {
            "type": "object_ref_array", "legal_refs": refs, "zone": "hand", "minimum": 0,
            "maximum": len(refs), "distinct": True, "payment": "reveal",
            "label": "Reveal X matching cards from your hand",
        },
    }


def revalidate_variable_colored_reveal_payment(host, actor, price, announced_x, records):
    if not isinstance(records, (tuple, list)):
        raise ValueError("The activation lost its retained reveal identities")
    try:
        refs = tuple(row["ref"] for row in records)
        actual = prepare_variable_colored_reveal_payment(host, actor, price, announced_x, refs)
        if tuple(FrozenMap(row) for row in records) != actual:
            raise ValueError("The selected reveal card changed before cost payment")
    except (TypeError, KeyError) as exc:
        raise ValueError("The retained reveal identities are malformed") from exc
    return actual


def variable_colored_reveal_proposal_details(host, actor, ability, announced_x, response):
    price = ability.variable_colored_reveal_price
    if price is None:
        return {}
    from .model import ActivationProposalError

    try:
        selected = prepare_variable_colored_reveal_payment(
            host, actor, price, announced_x, response.get("reveal_cards", ()),
        )
    except ValueError as exc:
        raise ActivationProposalError(str(exc), reason="invalid_colored_reveal_payment") from exc
    return {"variable_colored_reveal_payment": selected}


def commit_activation_reveal_price(host, proposal, source, ability, item):
    price = ability.variable_colored_reveal_price
    if price is None:
        return
    from .model import ActivationProposalError
    from ..action_proposals import thaw_json

    try:
        commit_variable_colored_reveal_payment(
            host, proposal.seat, price, thaw_json(proposal.details).get("variable_colored_reveal_payment"),
            item=item, source_ref=source.ref,
        )
    except ValueError as exc:
        raise ActivationProposalError(str(exc), reason="stale_colored_reveal_payment") from exc
