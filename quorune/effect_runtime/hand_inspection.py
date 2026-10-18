from __future__ import annotations

"""Authoritative knowledge mutation for one inspected player hand."""

from typing import Any

from ..errors import GameRuleError
from ..rules.hand_inspection import InspectHandIntent


def validate_entry_hand_reveal(host: Any,selection: Any) -> Any:
    """Preflight one retained private selection before any simultaneous move."""
    from collections.abc import Mapping
    if selection is None or selection=='declined':return None
    fields={'object_id','logical_object_id','ref','player','oracle_id','name'}
    if not isinstance(selection,Mapping) or set(selection)!=fields or any(type(selection[key]) is not str or not selection[key] for key in fields):
        raise GameRuleError('Entry hand reveal identity is malformed')
    actor=selection['player'];card=host.state.cards.get(selection['object_id'])
    if (actor not in host.active_seats or card is None or card.ref!=selection['ref']
            or card.logical_object_id!=selection['logical_object_id'] or card.zone!='hand'
            or card.owner!=actor or card.oracle_id!=selection['oracle_id']
            or card.object_id not in host.state.players[actor].zones['hand']):
        raise GameRuleError('Entry hand reveal lost its original private card')
    return card


def commit_entry_hand_reveal(host: Any,selection: Any,*,source_ref: str) -> None:
    """Publish the preflighted snapshot even when its card entered simultaneously."""
    commit_selected_hand_reveal(host, selection, source_ref=source_ref,
        summary='A card was revealed for battlefield entry.')


def commit_selected_hand_reveal(host: Any, selection: Any, *, source_ref: str, summary: str) -> None:
    """Publish only one selected card, retaining knowledge without a lasting reveal."""
    if selection is None or selection=='declined':return
    actor=selection['player'];card=host.state.cards.get(selection['object_id'])
    if card is not None and card.logical_object_id==selection['logical_object_id']:
        card.known_to=sorted(set(card.known_to).union(host.seats))
    host._log(actor,'card.reveal',summary,
        {'player':actor,'object':selection['ref'],'card':selection['name'],'source':source_ref},
        changed_objects=[selection['object_id']],changed_players=[actor])


def commit_hand_inspection(
    host: Any,
    intent: InspectHandIntent,
) -> tuple[str, ...]:
    """Record exactly one public reveal or controller-private hand look."""

    host._require_seat(intent.actor, in_game=True)
    host._require_seat(intent.player, in_game=True)
    hand_ids = tuple(host.state.players[intent.player].zones["hand"])
    cards = tuple(host.state.cards[object_id] for object_id in hand_ids)
    current_refs = tuple(card.ref for card in cards)
    if len(intent.refs) != len(current_refs) or set(intent.refs) != set(
        current_refs
    ):
        raise GameRuleError("The inspected hand changed")
    viewers = set(host.seats) if intent.public else {intent.actor}
    for card in cards:
        card.known_to = sorted(set(card.known_to).union(viewers))
        if intent.public:
            card.revealed_to = sorted(
                set(card.revealed_to).union(viewers)
            )
    host._log(
        intent.actor,
        "hand.reveal" if intent.public else "hand.look",
        (
            f"{intent.player} revealed their hand."
            if intent.public
            else f"{intent.actor} looked at {intent.player}'s hand."
        ),
        {
            "player": intent.player,
            "count": len(cards),
            "objects": list(intent.refs),
            "public": intent.public,
            "reason": intent.reason,
        },
        visibility=(None if intent.public else [intent.actor, "analyst"]),
        importance=1,
        changed_objects=hand_ids,
        changed_players=[intent.player],
    )
    return intent.refs


def begin_opening_hand_reveal(host,selection):
    """Disclose one selected final-hand card until first-turn start."""
    card=validate_entry_hand_reveal(host,selection)
    if card is None:raise GameRuleError('Opening reveal requires its selected hand card')
    prior=tuple(card.revealed_to)
    commit_selected_hand_reveal(host,selection,source_ref=card.ref,summary='A card was revealed from the opening hand.')
    card.revealed_to=sorted(set(card.revealed_to).union(host.seats))
    return prior


def end_opening_hand_reveal(host,*,object_id,logical_object_id,prior_revealed_to):
    """End only this duration, retaining learned identity and earlier disclosure."""
    card=host.state.cards.get(object_id)
    if card is not None and card.zone=='hand' and card.logical_object_id==logical_object_id:
        card.revealed_to=list(prior_revealed_to)


def _stack_cost_reveal_rows(item):
    from collections.abc import Mapping
    raw=item.context.get('casting_hand_reveals')
    if raw is None:return ()
    if (not isinstance(raw,Mapping) or set(raw)!={'schema_version','reveals'}
            or type(raw['schema_version']) is not int or raw['schema_version']!=1 or type(raw['reveals']) is not list):
        raise GameRuleError('Casting hand reveal frame is malformed')
    for row in raw['reveals']:
        if (not isinstance(row,Mapping) or set(row)!={'selection','previous_revealed_to','stack_ref'}
                or type(row['stack_ref']) is not str or type(row['previous_revealed_to']) is not list
                or any(type(s) is not str for s in row['previous_revealed_to'])
                or not isinstance(row['selection'],Mapping) or set(row['selection'])!={'object_id','logical_object_id','ref','player','oracle_id','name'}
                or any(type(v) is not str or not v for v in row['selection'].values())):
            raise GameRuleError('Casting hand reveal identity is malformed')
    return tuple(raw['reveals'])


def begin_stack_cost_hand_reveal(host,selection,*,item,source_ref):
    """CR701.20a: a revealed casting-cost card stays visible until stack departure."""
    card=validate_entry_hand_reveal(host,selection);prior=list(card.revealed_to)
    for pending in host.state.stack:
        for row in _stack_cost_reveal_rows(pending):
            if (row['stack_ref']==pending.ref and row['selection']['object_id']==card.object_id
                    and row['selection']['logical_object_id']==card.logical_object_id):
                prior=list(row['previous_revealed_to'])
                break
    rows=list(_stack_cost_reveal_rows(item));rows.append({'selection':dict(selection),'previous_revealed_to':prior,'stack_ref':item.ref})
    commit_selected_hand_reveal(host,selection,source_ref=source_ref,summary='A card was revealed to pay a casting cost.')
    card.revealed_to=sorted(set(card.revealed_to).union(host.seats))
    item.context['casting_hand_reveals']={'schema_version':1,'reveals':rows}


def cleanup_stack_cost_hand_reveals(host,item):
    """End only departing spell disclosures; overlapping spells keep their reveal."""
    rows=_stack_cost_reveal_rows(item)
    item.context.pop('casting_hand_reveals',None)
    for row in rows:
        if row['stack_ref']!=item.ref:continue
        selection=row['selection'];card=host.state.cards.get(selection['object_id'])
        if card is None or card.zone!='hand' or card.logical_object_id!=selection['logical_object_id']:continue
        active=any(other['stack_ref']==pending.ref and other['selection']['object_id']==card.object_id
            and other['selection']['logical_object_id']==card.logical_object_id
            for pending in host.state.stack if pending is not item for other in _stack_cost_reveal_rows(pending))
        if not active:card.revealed_to=list(row['previous_revealed_to'])


__all__ = ["commit_hand_inspection"]
