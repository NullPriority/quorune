from __future__ import annotations

"""Select an eligible public commander and freeze its current mana value."""

from ..object_query import ObjectQueryResult
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.owned_commander_amount import OWNED_COMMANDER_AMOUNT_FIELD, is_owned_commander_amount_effect
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


CANDIDATES_FIELD = "_owned_commander_amount_candidates"
_ZONES = ("battlefield", "command")
_COMMANDER_PROFILES = frozenset({"commander_duel", "commander_multiplayer"})
_EFFECT_FIELDS = {"op", "player", OWNED_COMMANDER_AMOUNT_FIELD}


def materialize_owned_commander_amount_rows(commanders, effective_data, actor, effect):
    if not is_owned_commander_amount_effect({key: effect[key] for key in _EFFECT_FIELDS if key in effect}, player=actor):
        raise SemanticChoiceError("Owned commander mana value belongs to the resolving controller")
    rows = []
    for card in commanders:
        if card.zone not in _ZONES:
            continue
        effective = effective_data(card)
        mana_value = effective.get("mana_value")
        if (type(mana_value) not in (int, float) or mana_value < 0
                or type(mana_value) is float and not mana_value.is_integer()):
            raise SemanticChoiceError("The eligible commander's effective mana value is unavailable")
        name = "Face-down permanent" if card.face_down else effective.get("name") or card.printed_name
        rows.append(ObjectQueryResult(
            object_id=card.object_id, ref=card.ref, logical_object_id=card.logical_object_id,
            printed_name=name, owner=card.owner, controller=card.controller,
            zone=card.zone, mana_value=mana_value, commander=True,
        ))
    return tuple(sorted(rows, key=lambda row: row.ref))


def _candidates(query):
    rows = query.objects(zones=_ZONES)
    if (any(not row.ref or not row.logical_object_id or not row.commander for row in rows)
            or len({row.ref for row in rows}) != len(rows)):
        raise SemanticChoiceError("Owned commander mana-value candidates lost public identities")
    return rows


def _snapshot(rows):
    return tuple((row.ref, row.logical_object_id, int(row.mana_value)) for row in rows)


def _ordered_results(player, count):
    if not count:
        return ()
    return (
        FrozenMap({"op": "draw", "player": player, "count": count, "private": True}),
        FrozenMap({"op": "lose_life", "player": player, "amount": count}),
    )


def prepare_owned_commander_amount(effect, context):
    actor = context.actor
    if (actor != context.stack_controller or actor not in context.query.active_seats
            or context.query.rules_profile not in _COMMANDER_PROFILES
            or not is_owned_commander_amount_effect(effect, player=actor)):
        raise SemanticChoiceError("Owned commander mana value belongs to the resolving controller")
    rows = _candidates(context.query)
    snapshot = _snapshot(rows)
    if len(rows) <= 1:
        value = snapshot[0][2] if rows else 0
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(
                reason="Use the sole eligible commander or zero",
                prepend_effects=_ordered_results(actor, value),
            ),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose an eligible commander you own to determine the amount.",
            choice=ObjectChoice(
                field_name="objects", legal_refs=tuple(row.ref for row in rows), zones=_ZONES,
                minimum=1, maximum=1, visibility="public",
            ),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"],
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows],
            }),
        ),
        continuation_effect=FrozenMap({**dict(effect), CANDIDATES_FIELD: snapshot}),
    )


def complete_owned_commander_amount(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    if (set(effect) != _EFFECT_FIELDS | {CANDIDATES_FIELD} or actor not in query.active_seats
            or query.rules_profile not in _COMMANDER_PROFILES
            or not is_owned_commander_amount_effect({key: effect[key] for key in _EFFECT_FIELDS}, player=actor)):
        raise SemanticChoiceError("Owned commander mana-value continuation lost its resolving controller")
    current = _snapshot(_candidates(query))
    if len(current) < 2 or thaw_value(effect[CANDIDATES_FIELD]) != thaw_value(current):
        raise SemanticChoiceError("The eligible commanders changed before selection")
    selected = response.get("objects")
    amounts = {ref: amount for ref, _logical_id, amount in current}
    if (not isinstance(selected, (list, tuple)) or len(selected) != 1
            or type(selected[0]) is not str or selected[0] not in amounts):
        raise SemanticChoiceError("Choose exactly one of your offered commanders")
    return SemanticChoiceCompletion(prepend_effects=_ordered_results(actor, amounts[selected[0]]))
