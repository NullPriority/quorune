"""Publish exactly the activating hand incarnation in the existing stack reveal ledger."""

from ..revealed_hand_zone_move import CAPABILITY, revealed_hand_entry_runtime_dependencies
from ...effect_runtime.hand_inspection import begin_stack_cost_hand_reveal
from ...errors import GameRuleError


def reveal_activated_hand_land_entry_source(host, proposal, source, item):
    program = host.semantics.get(item.semantic_key)
    if program is None or CAPABILITY not in program.capability_dependencies:
        return
    if (not revealed_hand_entry_runtime_dependencies(program) or item.controller != proposal.seat
            or source.zone != "hand" or source.owner != proposal.seat
            or item.source_object_id != source.object_id
            or item.context.get("source_logical_object_id") != source.logical_object_id):
        raise GameRuleError("The hand-source land activation lost its exact paid card")
    begin_stack_cost_hand_reveal(host, {
        "object_id": source.object_id, "logical_object_id": source.logical_object_id,
        "ref": source.ref, "player": proposal.seat, "oracle_id": source.oracle_id,
        "name": host.display_name(source.object_id),
    }, item=item, source_ref=source.ref, summary="The activating land was revealed from hand.")
