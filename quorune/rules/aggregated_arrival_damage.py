"""Deal one source's simultaneous damage from its committed artifact arrivals."""

from ..fixed_damage_set_model import FixedDamageSetSpec, PlayerDamageGroup, PlayerDamageRelation
from ..replacement.immutable import FrozenMap
from .aggregated_arrival_counters import MATCHED_ARRIVAL_COUNT


CAPABILITY = "damage.batch.matching_artifact_arrival_count"
_OPPONENTS = FixedDamageSetSpec((PlayerDamageGroup(PlayerDamageRelation.OPPONENTS),))
ARTIFACT_ARRIVAL_CONDITION = FrozenMap({"all": [
    {"field": "types", "op": "contains_any", "value": ["artifact"]},
    {"field": "controller", "op": "eq", "value": "$source.controller"},
]})


def artifact_arrival_damage_effect():
    return {"op": "damage_fixed_set", "source": "$source", "amount": MATCHED_ARRIVAL_COUNT,
            **_OPPONENTS.to_dict()}


def uses_artifact_arrival_damage(effect):
    return effect.get("op") == "damage_fixed_set" and effect.get("amount") == MATCHED_ARRIVAL_COUNT


def artifact_arrival_damage_capabilities(effect, target_schema, mechanics):
    from .damage_capability_shapes import _fixed_set_damage_capabilities

    if (target_schema is not None or "cr-120-damage" not in mechanics
            or FrozenMap(effect) != FrozenMap(artifact_arrival_damage_effect())):
        return ()
    fixed = _fixed_set_damage_capabilities({**effect, "amount": 1}, None, mechanics)
    return (CAPABILITY, *fixed) if fixed else ()


def artifact_arrival_damage_program_dependencies(program):
    effect = program.effects[0] if len(program.effects) == 1 else {}
    required = artifact_arrival_damage_capabilities(effect, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("permanent.enter", "battlefield")
            or "one_or_more_event_batch" not in program.coverage or program.handlers or program.cost_schema is not None
            or program.trigger_limit is not None or FrozenMap(program.event_condition or {}) != ARTIFACT_ARRIVAL_CONDITION):
        raise ValueError("Arrival damage requires the exact committed controlled-artifact batch trigger")
    return required
