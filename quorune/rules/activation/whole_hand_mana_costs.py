from collections.abc import Mapping

from ...errors import GameRuleError
from ...replacement.ordering import ReplacementChoiceRequired
from ...replacement.model import ReplacementEffectError
from ...replacement.immutable import thaw_value
from ...whole_hand_discard import whole_hand_input_snapshot
from ...zone_trigger_events import ZoneTransitionKind
from ..casting.whole_zone_cost_frame import (
    WHOLE_ZONE_COST_FRAME_FIELD, WHOLE_ZONE_SELECTION_FIELDS,
    WholeZoneCostChoiceRequired, WholeZoneCostFrame, WholeZonePaymentGroup,
)
from ...sacrifice_prohibition_model import sacrifice_is_prohibited
from ..whole_hand_mana import DISCARD_HAND_COST_FIELD


def validate_activation_frame(frame, response):
    summary = response.get("cost_summary")
    if (frame.schema_version != 1 or not isinstance(summary, Mapping)
            or type(summary.get(DISCARD_HAND_COST_FIELD)) is not int
            or summary[DISCARD_HAND_COST_FIELD] != 1 or type(summary.get("sac_self")) is not int
            or summary["sac_self"] != 1 or response.get("source") != frame.spell_ref
            or response.get("ability") != frame.option_id or response.get("from") != "battlefield"
            or WHOLE_ZONE_SELECTION_FIELDS.intersection(response) or "cost_objects" in response):
        raise ReplacementEffectError("Whole-hand mana continuation changed its exact activation or costs")


def commit_whole_hand_mana_cost(host, actor, source, ability, response):
    if (source.zone != "battlefield" or source.controller != actor or sacrifice_is_prohibited(host.state, source)
            or any(response.get(key) for key in (*WHOLE_ZONE_SELECTION_FIELDS, "cost_objects"))):
        raise GameRuleError("Whole-hand mana requires its own sacrificable source and whole current hand")
    hand = whole_hand_input_snapshot(host, {"op": "discard_whole_hands", "player": actor,
        "actor": actor, "players": [actor]}, actor=actor)["hands"][0]["members"]
    groups = (WholeZonePaymentGroup("hand", tuple((row["object_id"], row["logical_object_id"], row["ref"]) for row in hand)),
              WholeZonePaymentGroup("battlefield", ((source.object_id, source.logical_object_id, source.ref),)))
    previous = ()
    if WHOLE_ZONE_COST_FRAME_FIELD in response:
        frame = WholeZoneCostFrame.from_dict(response[WHOLE_ZONE_COST_FRAME_FIELD])
        validate_activation_frame(frame, response)
        if (frame.spell_ref, frame.option_id) != (source.ref, ability.ability_id):
            raise GameRuleError("Whole-hand mana cost lost its original source or ability")
        previous = frame.groups
    completed = []
    for index, group in enumerate(groups):
        if index < len(previous):
            retained = previous[index]
            if (retained.origin, retained.members) != (group.origin, group.members):
                raise GameRuleError("A whole-hand mana cost incarnation changed")
            group = retained
        if group.members:
            try:
                _pay_whole_group(host, group)
            except ReplacementChoiceRequired as required:
                frame = WholeZoneCostFrame(source.ref, ability.ability_id, (*completed, group))
                raise WholeZoneCostChoiceRequired(required, frame) from required
        completed.append(group)


def _pay_whole_group(host, group):
    kind = ZoneTransitionKind.DISCARD if group.origin == "hand" else ZoneTransitionKind.SACRIFICE
    changes = tuple((oid, "graveyard") for oid, _incarnation, _ref in group.members)
    host._move_cards_simultaneously(changes, reason="whole-hand mana activation cost",
        replacement_selections=tuple(thaw_value(value) for value in group.selections),
        transition_kinds={oid: kind for oid, _destination in changes})
