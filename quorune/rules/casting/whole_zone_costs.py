"""Pay both mandatory whole-zone costs through the existing cast transaction."""

from collections.abc import Mapping

from ...errors import GameRuleError
from ...replacement.immutable import thaw_value
from ...replacement.ordering import ReplacementChoiceRequired
from ...zone_trigger_events import ZoneTransitionKind
from ..casting_additional_costs import fixed_zone_change_cost_candidates
from ..island_pair_discard_cost import ISLAND_PAIR_DISCARD_VERSION
from ..fixed_payment_groups import (
    FIXED_DISCARD_SACRIFICE_FIELD, WHOLE_DISCARD_SACRIFICE_VERSION,
    fixed_discard_sacrifice_costs, fixed_discard_sacrifice_dependencies, fixed_discard_sacrifice_order,
)
from .model import CastProposalError
from .spell_context import fixed_cast_cost_object_context
from .whole_zone_cost_frame import (
    SELECTED_DISCARD_PAIR_FRAME_VERSION, WHOLE_ZONE_COST_FRAME_FIELD, WHOLE_ZONE_SELECTION_FIELDS,
    WholeZoneCostChoiceRequired, WholeZoneCostFrame, WholeZonePaymentGroup,
)


def _whole_cost_order(cost_schema, option, response):
    version = cost_schema.get(FIXED_DISCARD_SACRIFICE_FIELD) if isinstance(cost_schema, Mapping) else None
    offered = option.get(FIXED_DISCARD_SACRIFICE_FIELD)
    if offered is None and version != WHOLE_DISCARD_SACRIFICE_VERSION:
        if WHOLE_ZONE_COST_FRAME_FIELD in response:
            raise CastProposalError("Automatic zone cost continuation lost its price", reason="stale_whole_zone_cost")
        return ()
    try:
        dependencies = fixed_discard_sacrifice_dependencies(cost_schema)
        if (type(offered) is not int or type(version) is not int
                or offered != version or version != WHOLE_DISCARD_SACRIFICE_VERSION
                or not dependencies or option.get("additional_costs") != thaw_value(cost_schema["additional_costs"])
                or option.get("selected_additional_costs") or WHOLE_ZONE_SELECTION_FIELDS.intersection(response)):
            raise ValueError("Automatic zone price requires both mandatory cost sets")
        costs = fixed_discard_sacrifice_costs(option["additional_costs"])
        order = fixed_discard_sacrifice_order(option, option["additional_costs"])
        if not order:
            raise ValueError("Automatic zone price requires an offered order")
        return tuple(costs[position] for position in order)
    except (TypeError, ValueError) as exc:
        raise CastProposalError(str(exc), reason="additional_cost_malformed") from exc


def whole_zone_sacrifice_payable(host, actor):
    from ...sacrifice_prohibition_model import sacrifice_is_prohibited

    battlefield = (host.state.cards[object_id] for object_id in host.state.players[actor].zones["battlefield"])
    return not any(
        card.zone == "battlefield" and card.controller == actor and not card.phased_out
        and sacrifice_is_prohibited(host.state, card)
        for card in battlefield
    )


def _cost_objects(host, proposal, cost):
    if cost.origin_zone == "battlefield" and not whole_zone_sacrifice_payable(host, proposal.seat):
        raise CastProposalError("A permanent in the required payment set cannot be sacrificed",
                                reason="sacrifice_prohibited")
    refs = fixed_zone_change_cost_candidates(
        host, actor=proposal.seat, cost=cost, exclude_object_id=proposal.object_id,
    )
    return tuple(
        host._resolve_object(
            proposal.seat, ref, zones={cost.origin_zone},
            controlled_only=cost.origin_zone == "battlefield", owned_only=cost.origin_zone == "hand",
        )
        for ref in refs
    )


def _pay_zone_group(host, proposal, spell, result, payment, objects):
    if not objects:
        return
    if payment.origin == "hand":
        kind, transition = "discard", ZoneTransitionKind.DISCARD
    else:
        kind, transition = "sacrifice", ZoneTransitionKind.SACRIFICE
    result.paid_contexts.extend(fixed_cast_cost_object_context(host, obj) for obj in objects)
    moved = host._move_cards_simultaneously(
        tuple((obj.object_id, "graveyard") for obj in objects),
        reason=f"{spell.printed_name} {kind} cost",
        replacement_selections=tuple(thaw_value(selection) for selection in payment.selections),
        transition_kinds={obj.object_id: transition for obj in objects},
    )
    if tuple(obj.object_id for obj in moved) != tuple(obj.object_id for obj in objects):
        raise GameRuleError("Automatic zone payment did not complete every member of its set")
    for obj in moved:
        result.paid_refs.append(obj.ref)
        host._log(proposal.seat, f"cost.{kind}", f"{proposal.seat} paid {obj.ref} as a {kind} cost.",
                  {"spell": spell.ref, "object": obj.ref}, importance=1,
                  changed_objects=[obj.object_id], changed_players=[proposal.seat])


def _commit_island_pair_discard(host, proposal, response, spell, option, result, paid_facts):
    costs = option.get("additional_costs", ())
    if not any(raw.get("kind") == "zone_change" and raw.get("schema_version") == ISLAND_PAIR_DISCARD_VERSION
               for raw in costs):
        return False
    selections = option.get("selected_additional_costs", ())
    if len(costs) != 1 or len(selections) != 1 or paid_facts:
        raise CastProposalError("Island pair discard needs one exact selected payment",
                                reason="additional_cost_malformed")
    from .zone_cost_commit import _resolve_fixed_zone_change_additional_cost

    objects, price, _ = _resolve_fixed_zone_change_additional_cost(
        host, proposal, {**response, "_mana_replacement_selections": {}}, option, selections[0],
    )
    if not price.island_pair:
        raise CastProposalError("Island pair discard lost its exact price", reason="additional_cost_malformed")
    members = tuple((card.object_id, card.logical_object_id, card.ref) for card in objects)
    payment = WholeZonePaymentGroup("hand", members)
    if WHOLE_ZONE_COST_FRAME_FIELD in response:
        retained = WholeZoneCostFrame.from_dict(response[WHOLE_ZONE_COST_FRAME_FIELD])
        if (retained.schema_version != SELECTED_DISCARD_PAIR_FRAME_VERSION
                or (retained.spell_ref, retained.option_id) != (proposal.card_ref, proposal.cost_option_id)
                or retained.groups[0].members != members):
            raise CastProposalError("The selected discard incarnations changed", reason="stale_whole_zone_cost")
        payment = retained.groups[0]
    frame = WholeZoneCostFrame(proposal.card_ref, proposal.cost_option_id, (payment,),
                               schema_version=SELECTED_DISCARD_PAIR_FRAME_VERSION)
    frame.validate_response(response)
    try:
        _pay_zone_group(host, proposal, spell, result, payment, objects)
    except ReplacementChoiceRequired as required:
        raise WholeZoneCostChoiceRequired(required, frame) from required
    return True


def commit_whole_zone_costs(host, proposal, response, spell, option, program, result, paid_facts):
    """Rebuild each simultaneous set and seal the completed prefix on pauses."""
    if _commit_island_pair_discard(host, proposal, response, spell, option, result, paid_facts):
        return True
    order = _whole_cost_order(program.cost_schema if program is not None else None, option, response)
    if not order:
        return False
    if paid_facts:
        raise CastProposalError("Automatic prices have no single paid object", reason="additional_cost_unsupported")
    retained = ()
    if WHOLE_ZONE_COST_FRAME_FIELD in response:
        frame = WholeZoneCostFrame.from_dict(response[WHOLE_ZONE_COST_FRAME_FIELD])
        frame.validate_response(response)
        if (frame.schema_version != 1
                or (frame.spell_ref, frame.option_id) != (proposal.card_ref, proposal.cost_option_id)):
            raise CastProposalError("Automatic zone payment option changed", reason="stale_whole_zone_cost")
        retained = frame.groups
    completed = []
    for position, cost in enumerate(order):
        objects = _cost_objects(host, proposal, cost)
        members = tuple((obj.object_id, obj.logical_object_id, obj.ref) for obj in objects)
        previous = retained[position] if position < len(retained) else None
        if previous is not None and (previous.origin, previous.members) != (cost.origin_zone, members):
            raise CastProposalError("Automatic zone payment set changed", reason="stale_whole_zone_cost")
        payment = previous or WholeZonePaymentGroup(cost.origin_zone, members)
        try:
            _pay_zone_group(host, proposal, spell, result, payment, objects)
        except ReplacementChoiceRequired as required:
            frame = WholeZoneCostFrame(proposal.card_ref, proposal.cost_option_id, (*completed, payment))
            raise WholeZoneCostChoiceRequired(required, frame) from required
        completed.append(payment)
    return True
