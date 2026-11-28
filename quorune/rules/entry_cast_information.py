from __future__ import annotations

"""Read a real stack spell's cast receipt from canonical current-turn history.

This query does not assert that a later entry is the spell resolving. The
resolution/zone owner must establish that relationship before attaching it.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Mapping

from ..source_entry_cast_information import (CastSpellReceipt,EntryCastInformation,
    SOURCE_ENTRY_CAST_CONDITION,SOURCE_ENTRY_CAST_INFORMATION_FIELD, PAID_WHITE_ENTRY_TARGET_CAPABILITY)
from ..continuous_effect_model import ContinuousObjectIdentity
from ..turn_history import current_turn_history_events

_RESOLVING_ENTRY:ContextVar[object]=ContextVar('resolving_entry_cast_information',default=None)


def source_entry_cast_reader_present(host):
    registry=getattr(host,'semantics',None)
    programs=getattr(registry,'programs_for_event',None)
    if not callable(programs):
        return False
    from .entry_and_death_characteristics import NONHAND_ENTRY_PUMP_CAPABILITY

    return any(
        isinstance(getattr(program,'event_condition',None),Mapping) and program.event_condition.get('field')==SOURCE_ENTRY_CAST_CONDITION
        and host.semantic_program_is_current_trusted(program)
        for program in programs('permanent.enter.self')
    ) or any(
        NONHAND_ENTRY_PUMP_CAPABILITY in program.capability_dependencies
        and host.semantic_program_is_current_trusted(program)
        for program in programs('permanent.enter')
    )


def _program_reads_white_entry(host, program):
    return (program.event == "permanent.enter.self" and not program.provenance.get("granted_only")
            and PAID_WHITE_ENTRY_TARGET_CAPABILITY in program.capability_dependencies
            and host.semantic_program_is_current_trusted(program))


def _white_entry_reader_present(host):
    programs = getattr(getattr(host, "semantics", None), "programs_for_event", None)
    return callable(programs) and any(PAID_WHITE_ENTRY_TARGET_CAPABILITY in program.capability_dependencies
                                      for program in programs("permanent.enter.self"))


def _entry_reads_white_payment(host, card):
    if not _white_entry_reader_present(host):
        return False
    from ..trigger_discovery import _event_programs_for_source

    _, _, programs = _event_programs_for_source(
        host, card, "permanent.enter", source_zones=None, source_characteristics=None,
    )
    return any(_program_reads_white_entry(host, program) for program, _ in programs)


@contextmanager
def resolving_entry_cast_information(host, item):
    from .unpaid_creature_entry import reader_present as unpaid_entry_reader_present

    unpaid_reader = unpaid_entry_reader_present(host)
    cast_reader = source_entry_cast_reader_present(host) or unpaid_reader
    payment_reader = _white_entry_reader_present(host) or unpaid_reader
    if not cast_reader and not payment_reader:
        yield
        return
    card = host.state.cards.get(item.card_object_id or "")
    receipt = payment = None
    # A spell can move itself before generic resolution finishes.
    if card is not None and card.zone == "stack" and item.kind == "spell":
        if cast_reader:
            receipt = capture_stack_cast_receipt(host, card)
        if payment_reader and item.mana_spent_counts is not None:
            payment = item.mana_spent_counts
            payment.count_for(card, "W")
    token = _RESOLVING_ENTRY.set((host, item.card_object_id, receipt, payment))
    try:
        yield
    finally:
        _RESOLVING_ENTRY.reset(token)


def direct_token_entry_cast_context(host, card):
    if not source_entry_cast_reader_present(host):
        return {}
    if card.zone != "battlefield" or not card.is_token:
        raise ValueError("Direct token entry cast context requires the committed token")
    source = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
    return {SOURCE_ENTRY_CAST_INFORMATION_FIELD: EntryCastInformation(source, None).to_dict()}


def information_for_committed_entry(host, card, departure):
    if card.zone != "battlefield":
        return None
    from .unpaid_creature_entry import reader_present as unpaid_entry_reader_present

    unpaid_reader = unpaid_entry_reader_present(host)
    cast_reader = source_entry_cast_reader_present(host) or unpaid_reader
    payment_reader = _entry_reads_white_payment(host, card) or unpaid_reader
    if not cast_reader and not payment_reader:
        return None
    current = _RESOLVING_ENTRY.get()
    receipt = payment = None
    if current is not None and current[0] is host and current[1] == card.object_id and departure.origin == "stack":
        receipt = current[2]
        if receipt is not None and receipt.stack_logical_object_id != departure.logical_object_id:
            raise ValueError("The resolving entry changed its original stack incarnation")
        if payment_reader:
            payment = current[3]
            if payment is not None and payment.logical_object_id != departure.logical_object_id:
                raise ValueError("The resolving entry changed its original payment incarnation")
    source = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
    return EntryCastInformation(source, receipt, 2 if payment_reader else 1, payment).to_dict()


def source_entry_cast_value(host,context,*,controller,source,origin=None,any_caster=False):
    info=EntryCastInformation.from_dict(context.get(SOURCE_ENTRY_CAST_INFORMATION_FIELD))
    if controller not in host.state.players or info.cast is not None and (
            info.cast.caster not in host.state.players or info.cast.origin_owner not in host.state.players):
        raise ValueError('Source-entry cast information names an unknown player')
    if any_caster:
        if info.source != source or origin is not None:
            raise ValueError("An any-caster entry requires its exact original cast receipt")
        return info.cast is not None
    return info.was_cast_by(controller,source=source,origin=origin)


def nonhand_entrant_was_cast_by(host, context, *, controller):
    if context.get("from") != "stack":
        return False
    information = EntryCastInformation.from_dict(context.get(SOURCE_ENTRY_CAST_INFORMATION_FIELD))
    if information.source.logical_object_id != context.get("card_object_identity"):
        raise ValueError("A nonhand cast trigger requires the committed entering incarnation")
    if controller not in host.state.players:
        raise ValueError("A nonhand cast trigger requires the original active controller")
    cast = information.cast
    if cast is None:
        return False
    if cast.caster not in host.state.players or cast.origin_owner not in host.state.players:
        raise ValueError("An original cast entry names an unknown player")
    return cast.caster == controller and (cast.origin != "hand" or cast.origin_owner != controller)


def entry_condition_actual(host, field, condition, *, source, context):
    from ..errors import GameRuleError
    from ..source_entry_cast_information import validate_source_entry_cast_condition
    from .unpaid_creature_entry import CONDITION_FIELD, condition_actual

    from .entry_and_death_characteristics import NONHAND_ENTRY_PUMP_CONDITION

    try:
        if field == NONHAND_ENTRY_PUMP_CONDITION:
            if condition != {"field": NONHAND_ENTRY_PUMP_CONDITION, "op": "eq", "value": True}:
                raise ValueError("Expected the original nonhand cast entry predicate")
            return nonhand_entrant_was_cast_by(host, context, controller=source.controller)
        if field == CONDITION_FIELD:
            return condition_actual(condition, context, source)
        validate_source_entry_cast_condition(condition)
        return source_entry_cast_value(host, context, controller=source.controller,
            source=ContinuousObjectIdentity(source.object_id, source.logical_object_id),
            origin=condition.get('origin'), any_caster=condition.get('caster')=='any')
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc


def capture_stack_cast_receipt(host,card):
    if card.zone!='stack':return None
    items=tuple(item for item in host.state.stack
        if item.card_object_id==card.object_id and item.kind in {'spell','spell_copy'})
    if len(items)!=1:raise ValueError('Cast information requires one exact current stack spell')
    item=items[0]
    if item.kind=='spell_copy':return None
    records=tuple(event for event in current_turn_history_events(host.state.turn_history,
        turn_sequence=host.state.turn_sequence,kind='spell_cast')
        if event.object_incarnation==card.logical_object_id)
    if len(records)!=1:raise ValueError('The actual caster is unavailable or ambiguous in cast history')
    event=records[0];origin=item.context.get('cast_origin')
    if event.origin is not None and event.origin!=origin:
        raise ValueError('Cast history and stack origin disagree')
    if event.actor not in host.state.players or card.owner not in host.state.players:
        raise ValueError('A cast receipt cannot name an unknown player')
    return CastSpellReceipt(event.actor,origin,card.owner,item.ref,card.logical_object_id)
