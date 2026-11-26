"""Qualify one committed group of creature arrivals using actual cast spend."""

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from ..replacement.immutable import thaw_value
from ..source_entry_cast_information import EntryCastInformation


CAPABILITY = "trigger.event.unpaid_creature_entry_group"
CONDITION_FIELD = "unpaid_creature_entry_group_has_first_member"
GROUP_FIELD = "unpaid_creature_entry_members"
_GROUP = ContextVar("unpaid_creature_entry_group", default=None)
DEPS = (CAPABILITY, "trigger.event.normalized_zone_change", "trigger.effect.fixed_event",
        "trigger.placement.apnap", "casting.payment.mana_unit_counts", "zone.draw.library_to_hand")


def condition():
    return {"field": CONDITION_FIELD, "op": "eq", "value": True}


def reader_present(host):
    programs = getattr(getattr(host, "semantics", None), "programs_for_event", None)
    return callable(programs) and any(CAPABILITY in getattr(program, "capability_dependencies", ())
                                     and host.semantic_program_is_current_trusted(program)
                                     for program in programs("permanent.enter"))


def _entry_member(occurrence):
    info = EntryCastInformation.from_dict(occurrence.source_entry_cast_information)
    if (info.source.object_id, info.source.logical_object_id) != (occurrence.object_id, occurrence.current_logical_object_id):
        raise ValueError("Entry spend facts belong to another physical arrival")
    from ..zone_trigger_events import sealed_public_characteristic_facts

    facts = sealed_public_characteristic_facts(occurrence.current_characteristics)
    return {"identity": occurrence.current_logical_object_id, "controller": occurrence.current_controller,
            "creature": "creature" in facts["types"],
            "token": occurrence.token, "cast": info.cast is not None,
            "mana": sum(info.mana_spent_counts.amounts) if info.mana_spent_counts is not None else None}


@contextmanager
def capture_batch(host, occurrences):
    if not reader_present(host):
        yield
        return
    members = tuple(_entry_member(occurrence) for occurrence in occurrences
                    if occurrence.destination == "battlefield" and occurrence.origin != "battlefield")
    token = _GROUP.set((host, members))
    try:
        yield
    finally:
        _GROUP.reset(token)


def capture_group(host, context, occurrence):
    if not reader_present(host):
        return context
    batch = _GROUP.get()
    members = batch[1] if batch is not None and batch[0] is host else ()
    if not any(member["identity"] == occurrence.current_logical_object_id for member in members):
        members = (_entry_member(occurrence),)
    return {**dict(context), GROUP_FIELD: list(members)}


def token_members(host, contexts):
    if not reader_present(host):
        return ()
    return tuple({"identity": entry["card_object_identity"], "controller": entry["controller"],
                  "creature": "creature" in entry["types"], "token": True, "cast": False, "mana": None}
                 for entries in contexts for entry in entries)


def condition_actual(value, context, source):
    if thaw_value(value) != condition() or type(value["value"]) is not bool:
        raise ValueError("Unpaid creature arrivals require their exact occurrence condition")
    members = context.get(GROUP_FIELD)
    if not isinstance(members, (list, tuple)) or not members:
        raise ValueError("Unpaid creature arrivals require the original entry batch")
    arrivals = []
    seen = set()
    for member in members:
        if (not isinstance(member, Mapping)
                or set(member) != {"identity", "controller", "creature", "token", "cast", "mana"}
                or any(type(member[field]) is not str or not member[field] for field in ("identity", "controller"))
                or any(type(member[field]) is not bool for field in ("creature", "token", "cast"))
                or member["mana"] is not None and (type(member["mana"]) is not int or member["mana"] < 0)
                or member["identity"] in seen):
            raise ValueError("Malformed original creature arrival")
        seen.add(member["identity"])
        if (member["identity"] == source.logical_object_id
                or member["controller"] == source.controller and member["creature"] and not member["token"]):
            arrivals.append(member)
    return bool(arrivals and arrivals[0]["identity"] == context.get("card_object_identity")
                and all(not member["cast"] or member["mana"] == 0 for member in arrivals))


def runtime_dependencies(program):
    raw = thaw_value(program.event_condition)
    if (CAPABILITY not in getattr(program, "capability_dependencies", ())
            and (not isinstance(raw, dict) or raw.get("field") != CONDITION_FIELD)):
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter", "battlefield")
            or raw != condition() or type(raw["value"]) is not bool
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or thaw_value(program.effects) != [{"op":"draw","player":"$controller","count":1,"private":True}]):
        raise ValueError("Unpaid creature entry requires the exact grouped draw trigger")
    return DEPS
