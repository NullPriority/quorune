"""Select strict grouped departures instead of their source-pinned old views."""

import hashlib

from ..fixed_token_production import historical_unnamed_token_view
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.event_subscriptions import GRAVEYARD_DEPARTURE_LOOKBACK_EVENT
from ..rules.grouped_graveyard_departure import COVERAGE, grouped_graveyard_departure_condition
from .admission import CompleteCardProgramAdmission
from .binding import bind_semantic_program_runtime

_HASH_FIELDS = ("source_oracle_hash", "source_rulings_hash")
_GRANT_HANDLER = "continuous.ability.fixed-query-grant.v1"


def _trusted_current(program, oracle_id, oracle_hash, registry, profile):
    return (
        program.oracle_id == oracle_id and program.trust_level == "trusted"
        and not program.requires_arbiter
        and str(program.provenance.get("authored_by", "")).startswith("oracle-ir-v")
        and program.provenance.get("source_oracle_hash") == oracle_hash
        and bool(program.provenance.get("source_rulings_hash"))
        and bind_semantic_program_runtime(program, capability_registry=registry, profile=profile)["strict"]
    )


def _reviewed_source_matches(old, current):
    return (
        old.oracle_id == current.oracle_id and old.trust_level == "trusted"
        and old.provenance.get("review_status") == "reviewed" and not old.requires_arbiter
        and old.active_zone == "battlefield" and old.target_schema is None and old.cost_schema is None
        and old.trigger_limit is None and old.destination is None
        and all(old.provenance.get(key) == current.provenance.get(key) for key in _HASH_FIELDS)
    )


def _old_trigger_matches(old, current):
    if (
        not _reviewed_source_matches(old, current)
        or old.event != "card.leave_graveyard" or old.handlers
        or "one_or_more_event_batch" not in old.coverage
        or FrozenMap(old.event_condition or {}) != FrozenMap(grouped_graveyard_departure_condition())
    ):
        return False
    token, counter = current.effects
    former = historical_unnamed_token_view(token["name"], token["characteristics"])
    if former is None:
        return False
    name, characteristics = former
    return FrozenMap({"effects": old.effects}) == FrozenMap({"effects": (
        {**token, "name": name, "characteristics": characteristics},
        {"op": "counter_all_subtype", "player": "$controller", "subtype": name,
         "counter": counter["counter"], "amount": counter["amount"]},
    )})


def _grant_view(program):
    if program.event != "characteristics.evaluate" or program.effects or len(program.handlers) != 1:
        return None
    descriptor = thaw_value(FrozenMap(program.handlers[0]))
    if descriptor.get("handler_id") != _GRANT_HANDLER:
        return None
    query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), token=True)
    if FrozenMap(descriptor.get("condition", {})) != FrozenMap({
        "target_controller": "source_controller", "predicate": query.to_dict(), "exclude_source": False,
    }):
        return None
    modifier = descriptor.get("modifier", {})
    fragments = modifier.get("add_ability_fragments", ())
    if set(modifier) != {"add_ability_fragments"} or len(fragments) != 1:
        return None
    fragment, = fragments
    if fragment.get("kind") != "granted_activated":
        return None
    value = fragment["value"]
    return value.pop("semantic_key"), value.pop("ability_id"), FrozenMap(descriptor)


def shadowed_reviewed_grouped_graveyard_departure_keys(
    record, generated, reviewed, *, capability_registry, capability_profile, oracle_ir=None,
):
    """Remove old views only when the complete native program is trusted."""
    triggers = [program for program in generated
                if program.event == GRAVEYARD_DEPARTURE_LOOKBACK_EVENT and COVERAGE in program.coverage]
    if capability_registry is None or len(triggers) != 1 or len(generated) != 3:
        return set()
    if oracle_ir is None:
        from ..oracle_ir import compile_oracle_card

        oracle_ir = compile_oracle_card(
            record, capability_registry=capability_registry, capability_profile=capability_profile,
        )
    oracle_hash = hashlib.sha256(record.oracle_text.encode("utf-8")).hexdigest()
    if (oracle_ir.oracle_id != record.oracle_id or oracle_ir.oracle_hash != oracle_hash
            or not CompleteCardProgramAdmission.from_oracle_ir(oracle_ir).admitted):
        return set()
    if any(not _trusted_current(program, record.oracle_id, oracle_hash, capability_registry, capability_profile)
           for program in generated):
        return set()
    grants = [(program, view) for program in generated if (view := _grant_view(program)) is not None]
    if len(grants) != 1:
        return set()
    (grant, (child_key, child_id, grant_view)), = grants
    children = [program for program in generated
                if program.key == child_key and program.ability_id == child_id and program.event == "activate"
                and program.provenance.get("granted_only") is True
                and all(program.provenance.get(key) == grant.provenance.get(key) for key in _HASH_FIELDS)]
    if len(children) != 1:
        return set()
    trigger, = triggers
    if any(trigger.provenance.get(key) != grant.provenance.get(key) for key in _HASH_FIELDS):
        return set()
    old_triggers = [program for program in reviewed if _old_trigger_matches(program, trigger)]
    old_grants = [program for program in reviewed if _reviewed_source_matches(program, grant)
                  and program.event_condition is None and (view := _grant_view(program)) is not None
                  and view[0] == "builtin:mana-fixed" and view[2] == grant_view]
    if len(old_triggers) != 1 or len(old_grants) != 1:
        return set()
    return {old_triggers[0].key, old_grants[0].key}
