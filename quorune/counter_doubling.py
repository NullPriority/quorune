from __future__ import annotations

"""Current named-counter amounts delegated to the canonical placement owner."""

from typing import Mapping, Protocol, Sequence

from .affected_permanents import AffectedPermanentSetSpec, select_affected_permanents
from .counter_names import ALL_COUNTER_AMOUNTS, EXISTING_COUNTER_AMOUNT, normalized_counter_name, is_existing_counter_amount
from .counter_placement import CounterPlacementError, CounterPlacementHost, CounterPlacementRequest, place_counters, place_counters_on_refs
from .counter_placement_sets import CounterPlacementSetError, resolve_counter_placement_set, CounterPlacementSetHost
from .counter_placement_targets import CounterPlacementTargetQuery
from .object_query import ObjectQueryResult


class CounterDoublingError(CounterPlacementError):
    """A named-counter quantity or public subject is unavailable."""


class CounterDoublingHost(CounterPlacementHost, CounterPlacementTargetQuery, Protocol):
    pass


def snapshot_named_counter_doubling(rows: Sequence[ObjectQueryResult], *, actor: str,
    counter_name: str, source_ref: str | None = None) -> tuple[CounterPlacementRequest, ...]:
    """CR701.10e: snapshot each current count, before any replacements."""
    if type(actor) is not str or not actor or type(counter_name) is not str or not counter_name.strip():
        raise CounterDoublingError("Counter doubling requires actor and named kind")
    if source_ref is not None and (type(source_ref) is not str or not source_ref):
        raise CounterDoublingError("Counter doubling source must be a reference")
    name = normalized_counter_name(counter_name)
    subjects = tuple(rows)
    if any(not isinstance(row, ObjectQueryResult) for row in subjects):
        raise CounterDoublingError("Counter doubling requires typed public rows")
    for field in ("object_id", "logical_object_id", "ref"):
        identities = tuple(getattr(row, field) for row in subjects)
        if any(type(value) is not str or not value for value in identities) or len(set(identities)) != len(identities):
            raise CounterDoublingError("Counter doubling requires unique pinned subjects")
    requests = []
    for row in subjects:
        if row.zone != "battlefield" or row.phased_out or not row.known_to_actor:
            raise CounterDoublingError("Counter doubling requires known live permanents")
        if not isinstance(row.counters, Mapping):
            raise CounterDoublingError("Counter doubling quantity is unavailable")
        count = row.counters.get(name, 0)
        if type(count) is not int or count < 0:
            raise CounterDoublingError("Counter doubling quantity is unavailable")
        if count:
            requests.append(CounterPlacementRequest(subject_kind="permanent", subject_id=row.object_id,
                counter_name=name, amount=count, placing_player=actor, source_ref=source_ref))
    return tuple(requests)


def resolve_effect_counters_on_refs(host: CounterDoublingHost, *, actor, object_refs, counter_name,
    amount, selections=(), reason, source_ref=None):
    if amount==ALL_COUNTER_AMOUNTS:
        if counter_name is not None:raise CounterDoublingError('All-kind doubling cannot select a counter name')
        refs=tuple(object_refs)
        if any(type(ref) is not str or not ref for ref in refs) or len(set(refs))!=len(refs):raise CounterDoublingError('All-kind doubling requires unique public references')
        rows=host.counter_target_object_rows(actor,refs)
        if len(rows)!=len(refs) or {row.ref for row in rows}!=set(refs):raise CounterDoublingError('All-kind doubling subject knowledge is incomplete')
        return place_counters(host,snapshot_all_counter_doubling(rows,actor=actor,source_ref=source_ref),selections=selections,reason=reason)
    if type(amount) is int:
        return place_counters_on_refs(host, actor=actor, object_refs=object_refs, counter_name=counter_name,
            amount=amount, selections=selections, reason=reason, source_ref=source_ref)
    if amount != EXISTING_COUNTER_AMOUNT:
        raise CounterDoublingError("Unsupported counter amount")
    refs = tuple(object_refs)
    if any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
        raise CounterDoublingError("Counter doubling requires unique public references")
    rows = host.counter_target_object_rows(actor, refs)
    if len(rows) != len(refs) or {row.ref for row in rows} != set(refs):
        raise CounterDoublingError("Counter doubling subject knowledge is incomplete")
    requests = snapshot_named_counter_doubling(rows, actor=actor, counter_name=counter_name, source_ref=source_ref)
    return place_counters(host, requests, selections=selections, reason=reason)


def resolve_effect_counter_set(host: CounterPlacementSetHost, *, actor, spec: AffectedPermanentSetSpec,
    counter_name, amount, reason, source_ref=None, replacement_selections=()):
    if type(amount) is int:
        return resolve_counter_placement_set(host, actor=actor, spec=spec, counter_name=counter_name,
            amount=amount, reason=reason, source_ref=source_ref, replacement_selections=replacement_selections)
    if amount != EXISTING_COUNTER_AMOUNT:
        raise CounterPlacementSetError("Unsupported counter-set amount")
    if spec.exclude_source and source_ref is None:
        raise CounterPlacementSetError("Source-excluding counter sets require a source")
    try:
        rows = select_affected_permanents(host.affected_permanent_object_rows(actor), spec, actor=actor,
            active_seats=host.affected_permanent_active_seats(), apnap_order=host.affected_permanent_apnap_order(), source_ref=source_ref)
        requests = snapshot_named_counter_doubling(rows, actor=actor, counter_name=counter_name, source_ref=source_ref)
        return place_counters(host, requests, selections=replacement_selections, reason=reason)
    except CounterPlacementError as exc:
        raise CounterPlacementSetError(str(exc)) from exc


def named_counter_input_snapshot(host, effect, *, actor):
    """Seal quantity inputs and membership for an already pending instruction."""
    if effect.get('op')=='double_counters_on_targets':
        return all_counter_input_snapshot(host,effect,actor=actor)
    if not is_existing_counter_amount(effect.get('amount')):
        return None
    if effect.get('op')=='place_counters' and type(effect.get('card')) is str:
        rows=host.counter_target_object_rows(actor,(effect['card'],))
        if len(rows)!=1:
            raise CounterDoublingError('Named-counter pending subject is unavailable')
    elif effect.get('op')=='place_counters_on_set':
        spec=AffectedPermanentSetSpec.from_dict(effect.get('set'))
        rows=select_affected_permanents(host.affected_permanent_object_rows(actor),spec,actor=actor,
            active_seats=host.affected_permanent_active_seats(),apnap_order=host.affected_permanent_apnap_order(),source_ref=effect.get('source'))
    elif effect.get('op')=='place_counters_on_targets':
        refs=effect.get('cards')
        if not isinstance(refs,(list,tuple)) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs))!=len(refs):
            raise CounterDoublingError('Named-counter pending target set is malformed')
        rows=host.counter_target_object_rows(actor,tuple(refs))
        if len(rows)!=len(refs) or {row.ref for row in rows}!=set(refs):
            raise CounterDoublingError('Named-counter pending target knowledge is incomplete')
    else:
        raise CounterDoublingError('Named-counter pending instruction is malformed')
    # This also checks every known-empty versus malformed count before sealing.
    snapshot_named_counter_doubling(rows,actor=actor,counter_name=effect.get('counter'),source_ref=effect.get('source'))
    name=normalized_counter_name(effect['counter'])
    return [{'object_id':row.object_id,'logical_object_id':row.logical_object_id,'ref':row.ref,
             'count':row.counters.get(name,0)} for row in rows]


def snapshot_all_counter_doubling(rows,*,actor,source_ref=None):
    """Freeze every kind before the one canonical placement transaction."""
    if type(actor) is not str or not actor or source_ref is not None and (type(source_ref) is not str or not source_ref):
        raise CounterDoublingError('All-kind doubling needs a valid actor/source')
    subjects=tuple(rows)
    if any(not isinstance(row,ObjectQueryResult) for row in subjects):raise CounterDoublingError('All-kind doubling requires typed public rows')
    for field in ('object_id','logical_object_id','ref'):
        values=[getattr(row,field) for row in subjects]
        if any(type(value) is not str or not value for value in values) or len(set(values))!=len(values):raise CounterDoublingError('All-kind doubling requires unique pinned subjects')
    kinds=set()
    for row in subjects:
        if row.zone!='battlefield' or row.phased_out or not row.known_to_actor or not isinstance(row.counters,Mapping):
            raise CounterDoublingError('All-kind doubling requires known live counter maps')
        for name,count in row.counters.items():
            if type(name) is not str or not name or normalized_counter_name(name)!=name or type(count) is not int or count<0:
                raise CounterDoublingError('All-kind doubling counter map is malformed')
            kinds.add(name)
    return tuple(request for name in sorted(kinds) for request in snapshot_named_counter_doubling(subjects,actor=actor,counter_name=name,source_ref=source_ref))


def all_counter_input_snapshot(host,effect,*,actor):
    refs=effect.get('cards')
    if not isinstance(refs,(list,tuple)) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs))!=len(refs):
        raise CounterDoublingError('All-kind pending target set is malformed')
    rows=host.counter_target_object_rows(actor,tuple(refs))
    if len(rows)!=len(refs) or {row.ref for row in rows}!=set(refs):raise CounterDoublingError('All-kind pending target knowledge is incomplete')
    snapshot_all_counter_doubling(rows,actor=actor,source_ref=effect.get('source'))
    return [{'object_id':row.object_id,'logical_object_id':row.logical_object_id,'ref':row.ref,
        'counters':dict(sorted(row.counters.items()))} for row in rows]
