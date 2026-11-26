"""Retire a reviewed land-entry view only for its exact current trigger."""

from ..characteristic_fragments import DISTINCT_PUBLIC_NAMES_CAPABILITY
from ..fixed_token_production import historical_unnamed_token_view
from ..replacement.immutable import thaw_value
from ..rules.distinct_land_entry import distinct_land_entry_condition


def _current_replaces_reviewed(record, current, old):
    if (
        current.oracle_id != record.oracle_id or old.oracle_id != record.oracle_id
        or current.trust_level != "trusted" or old.trust_level != "trusted"
        or (current.event, old.event) != ("permanent.enter", "land.enter")
        or current.active_zone != "battlefield" or old.active_zone != current.active_zone
        or current.requires_arbiter or old.requires_arbiter or current.handlers or old.handlers
        or current.target_schema is not None or old.target_schema is not None
        or current.cost_schema is not None or old.cost_schema is not None
        or current.trigger_limit is not None or old.trigger_limit is not None
        or current.destination != old.destination or old.event_condition is not None
        or not {"land_entry", "triggered_ability", "landfall"} <= set(old.coverage)
        or len(current.effects) != 1 or len(old.effects) != 1
        or DISTINCT_PUBLIC_NAMES_CAPABILITY not in current.capability_dependencies
        or str(old.provenance.get("authored_by", "")).startswith("oracle-ir-v")
        or not str(current.provenance.get("authored_by", "")).startswith("oracle-ir-v")
        or not current.capability_closure or current.capability_closure.get("trusted") is not True
        or any(not current.provenance.get(key) or current.provenance[key] != old.provenance.get(key)
               for key in ("source_oracle_hash", "source_rulings_hash"))
    ):
        return False
    previous = thaw_value(old.effects[0])
    minimum = previous.get("minimum_distinct_names")
    if type(minimum) is not int or minimum < 2:
        return False
    if thaw_value(current.event_condition) != distinct_land_entry_condition(minimum):
        return False
    effect = thaw_value(current.effects[0])
    if (set(effect) != {"op", "controller", "name", "quantity", "characteristics"}
            or type(effect["quantity"]) is not int
            or (effect["op"], effect["controller"], effect["quantity"]) != ("create_token", "$controller", 1)):
        return False
    token = historical_unnamed_token_view(effect["name"], effect["characteristics"])
    if token is None:
        return False
    name, characteristics = token
    return previous == {
        "op": "create_token_if_distinct_controlled_names", "controller": "$controller",
        "required_type": "land", "minimum_distinct_names": minimum,
        "token": {"name": name, "characteristics": characteristics},
    }


def shadowed_reviewed_distinct_land_entry_keys(record, generated, reviewed, *, capability_registry, capability_profile):
    if capability_registry is None or record.faces:
        return set()
    from .binding import bind_semantic_program_runtime

    return {old.key for old in reviewed if sum(
        _current_replaces_reviewed(record, current, old)
        and bind_semantic_program_runtime(current, capability_registry=capability_registry,
                                          profile=capability_profile)["strict"]
        for current in generated
    ) == 1}
