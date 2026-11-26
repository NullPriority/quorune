"""One optional creature target per player, with a fixed source reward after destruction."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY, selected_target_count_descriptor
from .committed_quantity_followups import CommittedQuantityFollowup


SOURCE_COUNTER_REWARD_KIND = "controller_creature_destruction_source_counters"
SOURCE_COUNTER_REWARD_MECHANIC = "controller-creature-destruction-source-counters"
SOURCE_COUNTER_REWARD_CAPABILITY = "permanent.destroy.controller_creature_source_counter_reward"
SOURCE_COUNTER_REWARD_TEMPLATE = "per-player-creature-destruction-source-counter-reward-v1"
CREATURE_TARGET_ROLES_FIELD = "one_optional_creature_for_each_player"
_TARGET_ROLE_SEATS_CONTEXT = "per_player_creature_target_seats"

_DEPENDENCIES = (
    SOURCE_COUNTER_REWARD_CAPABILITY, "target.set.one_per_player", ANY_NUMBER_TARGET_CAPABILITY,
    "target.revalidate_resolution", "permanent.destroy.effect",
    "resolution.effect.fixed_homogeneous_target_set", "counter.producer.fixed_effect",
)


def _creature_role_group():
    return {"id": "creature_role", "zones": ["battlefield"], "categories": ["permanent"],
            "types_any": ["creature"], "min": 0, "max": 1}


def creature_target_role_schema():
    return {CREATURE_TARGET_ROLES_FIELD: True, "globally_distinct": True, "groups": [_creature_role_group()]}


def _has_role_template(schema):
    return isinstance(schema, Mapping) and CREATURE_TARGET_ROLES_FIELD in schema


def creature_role_trigger_context(schema, host, source):
    if not _has_role_template(schema):
        return {}
    if FrozenMap(schema) != FrozenMap(creature_target_role_schema()):
        raise ValueError("Per-player creature targeting has a malformed role template")
    return {
        _TARGET_ROLE_SEATS_CONTEXT: list(host.active_seats),
        "source_logical_object_id": source.logical_object_id,
    }


def bound_creature_role_schema(schema, context, seats):
    if not _has_role_template(schema):
        return schema
    if FrozenMap(schema) != FrozenMap(creature_target_role_schema()):
        raise ValueError("Per-player creature targeting has a malformed role template")
    roles = context.get(_TARGET_ROLE_SEATS_CONTEXT)
    if (not isinstance(roles, (tuple, list)) or not roles
            or any(type(role) is not str or role not in seats for role in roles)
            or len(roles) != len(set(roles))):
        raise ValueError("Per-player creature targeting lost its original role seats")
    return {
        "globally_distinct": True,
        "groups": [{**_creature_role_group(), "id": f"seat_{seat.casefold()}", "controller_seat": seat} for seat in roles],
    }


@dataclass(frozen=True, slots=True)
class ControlledDestructionSourceCounters:
    stack_ref: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.stack_ref) is not str or not self.stack_ref
                or self.stack_ref != "$stack" and self.stack_ref.startswith("$")):
            raise ValueError("Destruction reward requires the original ability stack reference")

    def to_dict(self):
        return {"kind": SOURCE_COUNTER_REWARD_KIND, "schema_version": self.schema_version,
                "stack_ref": self.stack_ref, "counter": "+1/+1", "amount": 2}

    @classmethod
    def from_dict(cls, value, *, resolved=False):
        if not isinstance(value, Mapping):
            raise ValueError("Destruction reward requires its complete typed continuation")
        spec = cls(value.get("stack_ref"), value.get("schema_version"))
        if (type(value.get("amount")) is not int or FrozenMap(value) != FrozenMap(spec.to_dict())
                or resolved != (spec.stack_ref != "$stack")):
            raise ValueError("Destruction reward requires its fixed source placement and stack binding")
        return spec


def uses_controlled_destruction_source_counters(effect):
    followup = effect.get("followup")
    return isinstance(followup, Mapping) and followup.get("kind") == SOURCE_COUNTER_REWARD_KIND


def controlled_destruction_source_counter_effect():
    return {"op": "destroy_targets", "cards": "$targets", "maximum_targets": selected_target_count_descriptor(),
            "announced_target_count": True, "followup": ControlledDestructionSourceCounters("$stack").to_dict()}


def controlled_destruction_source_counter_dependencies(effect):
    if FrozenMap(effect) != FrozenMap(controlled_destruction_source_counter_effect()):
        raise ValueError("Destruction reward requires the entire announced target result")
    ControlledDestructionSourceCounters.from_dict(effect["followup"])
    return _DEPENDENCIES


def controlled_destruction_source_counter_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (SOURCE_COUNTER_REWARD_MECHANIC not in mechanic_ids or len(effects) != 1
            or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != FrozenMap(creature_target_role_schema())):
        return ()
    return controlled_destruction_source_counter_dependencies(effects[0])


def controlled_destruction_source_counter_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if (not _has_role_template(program.target_schema) and SOURCE_COUNTER_REWARD_MECHANIC not in program.coverage
            and not any(uses_controlled_destruction_source_counters(effect)
                        for effect in nested_effects((*program.effects, *getattr(program, "handlers", ()))))):
        return ()
    required = controlled_destruction_source_counter_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.cost_schema is not None or program.handlers):
        raise ValueError("Destruction reward requires its single grouped self-entry trigger")
    return required


def closed_controlled_destruction_source_counter_program(program):
    try:
        required = controlled_destruction_source_counter_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def decode_announced_destruction_followup(value, *, resolved=False):
    if isinstance(value, Mapping) and value.get("kind") == SOURCE_COUNTER_REWARD_KIND:
        return ControlledDestructionSourceCounters.from_dict(value, resolved=resolved)
    from .announced_destruction_controller_tokens import AnnouncedDestructionControllerTokens

    return AnnouncedDestructionControllerTokens.from_dict(value)


def commit_controlled_destruction_source_counters(host, intent):
    from ..destruction import destroy_permanent_refs

    reward = intent.followup
    item = next((item for item in host.state.stack if item.ref == reward.stack_ref), None)
    if (item is None or item.kind not in {"triggered_ability", "triggered_ability_copy"}
            or item.controller != intent.actor or item.context.get("currently_resolving") is not True
            or tuple(ref for ref in item.targets if ref is not None) != intent.object_refs):
        raise GameRuleError("Destruction reward requires the original resolving ability and live target slots")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not closed_controlled_destruction_source_counter_program(program)
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("Destruction reward requires its admitted grouped entry trigger")
    identity = item.context.get("source_logical_object_id")
    if type(identity) is not str or not identity:
        raise GameRuleError("Destruction reward lost its original entry incarnation")

    before = (host._resolve_object(intent.actor, ref, zones={"battlefield"}) for ref in intent.object_refs)
    controlled_ids = {card.object_id for card in before if card.controller == intent.actor}
    result = destroy_permanent_refs(host, intent.object_refs, actor=intent.actor,
        reason=intent.reason, replacement_selections=intent.replacement_selections)
    original = host.state.cards.get(item.source_object_id)
    if (not controlled_ids.intersection(result.destroyed_object_ids) or original is None
            or original.zone != "battlefield" or original.phased_out or original.logical_object_id != identity):
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        "op": "place_counters", "card": "$source.zone_object", "source": "$source",
        "counter": "+1/+1", "amount": 2,
    }),))
