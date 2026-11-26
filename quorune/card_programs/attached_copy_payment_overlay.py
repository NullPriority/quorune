"""Use the full current Bestow and attached-copy graph for newly admitted cards."""

import hashlib

from ..bestow import BESTOW_HANDLER_ID, BESTOW_RUNTIME_EVENT, FixedManaBestowSpec
from ..fixed_token_production import historical_unnamed_token_view
from ..replacement.immutable import FrozenMap
from ..rules.attached_copy_payment import attached_copy_payment_parts, uses_attached_copy_payment
from .admission import CompleteCardProgramAdmission
from .binding import bind_semantic_program_runtime

_HASH_FIELDS = ("source_oracle_hash", "source_rulings_hash")


def _current_bestow(program):
    if len(program.handlers) == 1 and program.event == BESTOW_RUNTIME_EVENT:
        descriptor, = program.handlers
        if descriptor.get("handler_id") == BESTOW_HANDLER_ID:
            return FixedManaBestowSpec.from_dict(descriptor["bestow"])
    return None


def _source_matches(old, current):
    return (old.oracle_id == current.oracle_id and old.trust_level == "trusted"
            and old.provenance.get("review_status") == "reviewed" and not old.requires_arbiter
            and old.event_condition is None and old.target_schema is None
            and old.trigger_limit is None and not old.handlers
            and all(old.provenance.get(name) == current.provenance.get(name) for name in _HASH_FIELDS))


def _legacy_bestow_matches(old, current, bestow):
    if (not _source_matches(old, current) or old.event != "resolve" or old.active_zone != "stack"
            or old.destination != "battlefield" or old.effects):
        return False
    option = bestow.cast_cost_option()
    option.pop("bestow_fingerprint")
    option["requirements"] = {color: quantity for color, quantity in option["requirements"].items() if quantity}
    return isinstance(old.cost_schema, dict) and FrozenMap(old.cost_schema) == FrozenMap({"alternate_costs": [option]})


def _legacy_trigger_matches(old, current, payment, fallback):
    if (not _source_matches(old, current) or old.event != "land.enter" or old.active_zone != "battlefield"
            or old.destination is not None or old.cost_schema is not None):
        return False
    definition = historical_unnamed_token_view(fallback["name"], fallback["characteristics"])
    if definition != ("Insect", {"type_line": "Token Creature — Insect", "colors": ["G"],
                                  "power": "1", "toughness": "1"}):
        return False
    cost = {color: quantity for color, quantity in payment.requirements.items() if quantity}
    return FrozenMap({"effects": old.effects}) == FrozenMap({"effects": [
        {"op": "springheart_landfall", "player": "$controller", "source": "$source", "cost": cost},
    ]})


def shadowed_reviewed_attached_copy_payment_keys(record, generated, reviewed, *, capability_registry,
                                               capability_profile, oracle_ir=None):
    triggers = [program for program in generated if len(program.effects) == 1
                and uses_attached_copy_payment(program.effects[0])]
    if capability_registry is None or len(triggers) != 1 or len(generated) != 3:
        return set()
    trigger, = triggers
    if oracle_ir is None:
        from ..oracle_ir import compile_oracle_card

        oracle_ir = compile_oracle_card(record, capability_registry=capability_registry,
                                        capability_profile=capability_profile)
    oracle_hash = hashlib.sha256(record.oracle_text.encode("utf-8")).hexdigest()
    if (oracle_ir.oracle_id != record.oracle_id or oracle_ir.oracle_hash != oracle_hash
            or not CompleteCardProgramAdmission.from_oracle_ir(oracle_ir).admitted):
        return set()
    if any(program.oracle_id != record.oracle_id or program.trust_level != "trusted" or program.requires_arbiter
           or not str(program.provenance.get("authored_by", "")).startswith("oracle-ir-v")
           or program.provenance.get("source_oracle_hash") != oracle_hash
           or not program.provenance.get("source_rulings_hash")
           or any(program.provenance.get(field) != trigger.provenance.get(field) for field in _HASH_FIELDS)
           or not bind_semantic_program_runtime(program, capability_registry=capability_registry,
                                                 profile=capability_profile)["strict"]
           for program in generated):
        return set()
    bestow_views = [(program, spec) for program in generated if (spec := _current_bestow(program)) is not None]
    if len(bestow_views) != 1 or not any(program.event == "characteristics.evaluate" for program in generated):
        return set()
    (current_bestow, bestow), = bestow_views
    payment, _, fallback = attached_copy_payment_parts(trigger.effects[0])
    old_bestow = [program for program in reviewed if _legacy_bestow_matches(program, current_bestow, bestow)]
    old_triggers = [program for program in reviewed if _legacy_trigger_matches(program, trigger, payment, fallback)]
    if len(old_bestow) != 1 or len(old_triggers) != 1:
        return set()
    return {old_bestow[0].key, old_triggers[0].key}
