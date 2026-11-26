"""An ordinary destruction can produce two copies after its actual death receipt."""
from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


DESTROYED_CREATURE_COPY_KIND = "destroyed_creature_half_copies"
DESTROYED_CREATURE_COPY_CAPABILITY = "permanent.destroy.actual_death_copy_tokens"
COMMITTED_DEATH_COPY_FIELD = "committed_death_copy"
_CREATURE_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1,
})


@dataclass(frozen=True, slots=True)
class DestroyedCreatureCopy:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Death copy requires its exact destruction result version")

    def to_dict(self):
        return {"kind": DESTROYED_CREATURE_COPY_KIND, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"kind", "schema_version"}
                or value["kind"] != DESTROYED_CREATURE_COPY_KIND):
            raise ValueError("Death copy requires its complete destruction descriptor")
        return cls(value["schema_version"])

    def effect(self):
        return {"op": "destroy", "card": "$target.0", "followup": self.to_dict()}


def uses_destroyed_creature_copy(effect):
    followup = effect.get("followup") if isinstance(effect, Mapping) else None
    return (isinstance(followup, Mapping) and effect.get("op") == "destroy"
            and followup.get("kind") == DESTROYED_CREATURE_COPY_KIND)


def destroyed_creature_copy_dependencies(effect, target_schema):
    if (not uses_destroyed_creature_copy(effect) or set(effect) != {"op", "card", "followup"}
            or effect["card"] != "$target.0" or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int or FrozenMap(target_schema) != _CREATURE_TARGET):
        raise ValueError("Death copy requires its single exact creature target")
    DestroyedCreatureCopy.from_dict(effect["followup"])
    return (DESTROYED_CREATURE_COPY_CAPABILITY, "permanent.destroy.effect", "target.revalidate_resolution",
            "token.creation.fixed_copy", "token.creation.fixed_definition", "zone.change.destination_replacement")


def destroyed_creature_copy_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_destroyed_creature_copy(effect))
    if not marked and DESTROYED_CREATURE_COPY_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
            or (program.event, program.active_zone) != ("resolve", "stack") or program.cost_schema is not None):
        raise ValueError("Death copy requires its original direct spell resolution")
    return destroyed_creature_copy_dependencies(marked[0], program.target_schema)



def commit_destroyed_creature_copy(host, intent):
    """Read the last battlefield values, then use the ordinary destruction owner."""
    from ..destruction import destroy_permanent_refs
    from ..errors import GameRuleError
    from ..token_creation_identity import token_copy_snapshot
    from ..token_copy_recipes import SIGNED_COPY_BASE_STATS_VERSION, TokenCopyExceptionSpec, apply_copy_exception
    from .committed_quantity_followups import CommittedQuantityFollowup

    item = host.state.stack[-1] if host.state.stack else None
    card = host._resolve_object(intent.actor, intent.object_ref, zones={"battlefield"})
    if (item is None or item.controller != intent.actor or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or list(item.targets) != [card.ref]):
        raise GameRuleError("Death copy lost its revalidated destruction target")
    controller = card.controller
    object_id, ref, counter = card.object_id, card.ref, card.zone_change_counter
    power = (host._numeric_stat(object_id, "power") + 1) // 2
    toughness = (host._numeric_stat(object_id, "toughness") + 1) // 2
    snapshot = token_copy_snapshot(host, card)
    exception = TokenCopyExceptionSpec(power=power, toughness=toughness, schema_version=SIGNED_COPY_BASE_STATS_VERSION)
    characteristics = apply_copy_exception(snapshot["characteristics"], exception)
    snapshot["characteristics"] = characteristics
    snapshot["annotations"]["copy_overrides"] = characteristics
    result = destroy_permanent_refs(
        host, (ref,), actor=intent.actor, reason=intent.reason, replacement_selections=intent.replacement_selections,
        record_graveyard_outcomes=True,
    )
    if object_id not in result.died_object_ids:
        return CommittedQuantityFollowup(())
    effect = {"op": "create_token", "controller": controller, "quantity": 2, "copy_of": ref,
              COMMITTED_DEATH_COPY_FIELD: {"schema_version": 1, "stack_ref": item.ref, "object_id": object_id,
                                           "zone_change_counter": counter, "snapshot": snapshot}}
    return CommittedQuantityFollowup((FrozenMap(effect),))


def committed_death_copy_controller(host, effect, item):
    """Validate the persisted copy receipt against the still-resolving spell."""
    from ..errors import GameRuleError

    raw = effect.get(COMMITTED_DEATH_COPY_FIELD)
    scope = {"op", "controller", "quantity", "copy_of", COMMITTED_DEATH_COPY_FIELD}
    optional = {"reason", "_replacement_selections", "_runtime_source"}
    if (not scope <= set(effect) <= scope | optional
            or not isinstance(raw, Mapping) or set(raw) != {"schema_version", "stack_ref", "object_id", "zone_change_counter", "snapshot"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or item is None or item.ref != raw["stack_ref"] or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or list(item.targets) != [effect["copy_of"]]
            or effect["op"] != "create_token" or type(effect["quantity"]) is not int or effect["quantity"] != 2
            or type(raw["zone_change_counter"]) is not int
            or any(type(val) is not str or not val for val in (raw["object_id"], effect["controller"]))):
        raise GameRuleError("Death copy continuation lost its original typed spell or target")
    choice = item.context.get("target_snapshots", {}).get(effect["copy_of"], {})
    if (choice.get("object_id") != raw["object_id"]
            or choice.get("zone_change_counter") != raw["zone_change_counter"]):
        raise GameRuleError("Death copy continuation changed the selected target incarnation")
    program = host.semantics.get(item.semantic_key)
    if (program is None or len(program.effects) != 1 or not uses_destroyed_creature_copy(program.effects[0])
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("Death copy continuation needs its original trusted destruction")
    snapshot = raw["snapshot"]
    annotations = snapshot.get("annotations") if isinstance(snapshot, Mapping) else None
    if not isinstance(annotations, Mapping) or annotations.get("copied_from") != raw["object_id"]:
        raise GameRuleError("Death copy snapshot refers to another object")
    return effect["controller"]


def apply_committed_death_copy(host, effect, *, actor, reason):
    """Create the two sealed copies through the authentic token and Aura owners."""
    from ..copied_token_aura_entry import copied_aura_token_selections, clear_copied_aura_token_selections
    from ..errors import GameRuleError
    from ..token_creation import create_tokens

    item = host.state.stack[-1] if host.state.stack else None
    controller = committed_death_copy_controller(host, effect, item)
    if item.controller != actor:
        raise GameRuleError("Death copy token creation requires its resolving controller")
    if controller not in host.active_seats:
        return []
    created = create_tokens(
        host, controller, name="", quantity=2, copy_of=effect["copy_of"], copy_source_zone="battlefield",
        copy_snapshot=effect[COMMITTED_DEATH_COPY_FIELD]["snapshot"], reason=reason,
        replacement_selections=tuple(effect.get("_replacement_selections", ())),
        copied_aura_selections=copied_aura_token_selections(item),
    )
    clear_copied_aura_token_selections(item)
    return created
