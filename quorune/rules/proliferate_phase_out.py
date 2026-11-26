from __future__ import annotations

"""The second choice after a Proliferate batch has actually placed its counters."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..phasing import phase_out_permanents
from ..phasing_model import PHASE_OUT_CAPABILITY, validate_phase_out_recipient_identities, validate_selected_counter_phase_out
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup


PROLIFERATE_PHASE_OUT_CAPABILITY = "counter.producer.proliferate_controlled_phase_out"
PROLIFERATE_PHASE_OUT_FIELD = "proliferate_phase_out_version"
PROLIFERATE_PHASE_CHOICE_FIELD = "proliferate_phase_out_choice_version"


def proliferate_phase_out_effect():
    return {"op": "proliferate", PROLIFERATE_PHASE_OUT_FIELD: 1}


def uses_proliferate_phase_out(effect):
    return isinstance(effect, Mapping) and PROLIFERATE_PHASE_OUT_FIELD in effect


def uses_proliferate_phase_choice(effect):
    return isinstance(effect, Mapping) and PROLIFERATE_PHASE_CHOICE_FIELD in effect


def validate_proliferate_phase_out(effect):
    if (type(effect.get(PROLIFERATE_PHASE_OUT_FIELD)) is not int
            or dict(effect) != proliferate_phase_out_effect()):
        raise ValueError("Proliferate phasing requires its complete versioned instruction")
    return ("counter.producer.proliferate", PHASE_OUT_CAPABILITY, PROLIFERATE_PHASE_OUT_CAPABILITY)


def proliferate_phase_out_program_dependencies(program):
    if not any(uses_proliferate_phase_out(effect) for effect in program.effects):
        return ()
    if (len(program.effects) != 1 or program.event != "resolve" or program.active_zone != "stack"
            or program.target_schema is not None):
        raise ValueError("Proliferate phasing requires one complete untargeted spell instruction")
    return validate_proliferate_phase_out(program.effects[0])


def counter_phase_choice_receipts(effect):
    if (set(effect) != {"op", PROLIFERATE_PHASE_CHOICE_FIELD, "recipients"}
            or effect["op"] != "proliferate"
            or type(effect[PROLIFERATE_PHASE_CHOICE_FIELD]) is not int
            or effect[PROLIFERATE_PHASE_CHOICE_FIELD] != 1):
        raise ValueError("Counter phasing requires its committed-counters choice")
    return validate_phase_out_recipient_identities(effect["recipients"])


def committed_proliferate_phase_choice(host, actor, results):
    positive = {result.subject_id for result in results
                if result.subject_kind == "permanent" and result.after > result.before}
    cards = [host.state.cards[object_id] for object_id in positive if object_id in host.state.cards]
    rows = [
        {"object_id": card.object_id, "logical_object_id": card.logical_object_id, "ref": card.ref}
        for card in sorted(cards, key=lambda candidate: candidate.ref)
        if card.zone == "battlefield" and card.controller == actor and not card.phased_out
    ]
    if not rows:
        return CommittedQuantityFollowup(())
    effect = {"op": "proliferate", PROLIFERATE_PHASE_CHOICE_FIELD: 1, "recipients": rows}
    return CommittedQuantityFollowup((FrozenMap(effect),))


def phase_out_selected_counter_recipients(host, effect, *, actor, reason):
    receipts = validate_selected_counter_phase_out(effect, runtime=True)
    identities = []
    for row in receipts:
        card = host.state.cards.get(row["object_id"])
        if (card is None or card.logical_object_id != row["logical_object_id"] or card.ref != row["ref"]
                or card.zone != "battlefield" or card.controller != actor or card.phased_out):
            raise GameRuleError("A chosen counter recipient changed before phasing")
        identities.append(ContinuousObjectIdentity(card.object_id, card.logical_object_id))
    return phase_out_permanents(host, tuple(identities), actor=actor, reason=reason)
