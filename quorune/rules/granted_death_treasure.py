"""The granted tapped owner return and its independent fixed Treasure."""

from collections.abc import Mapping

from ..ability_fragments import GrantedTriggeredAbilitySpec, canonical_ability_fragments
from ..replacement.immutable import FrozenMap
from ..targets import TargetGroup
from .granted_death_role_return import GRANTED_SELF_DEATH_RETURN_CAPABILITY, granted_self_death_return_effect
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities

CAPABILITY = "zone.return.granted_death_and_independent_treasure"
CHILD_TEMPLATE = "granted-death-tapped-owner-return-and-treasure-v1"
PARENT_TEMPLATE = "temporary-granted-death-return-and-treasure-eot-v1"


def granted_death_treasure_effect_dependencies(effects):
    if len(effects) != 2 or FrozenMap(effects[0]) != FrozenMap(granted_self_death_return_effect()):
        return ()
    treasure = effects[1]
    if (not isinstance(treasure, Mapping)
            or set(treasure) != {"op", "controller", "name", "quantity", "characteristics"}
            or treasure["op"] != "create_token" or treasure["controller"] != "$controller"
            or treasure["name"] != "Treasure Token"
            or type(treasure["quantity"]) is not int or treasure["quantity"] != 1
            or not isinstance(treasure["characteristics"], Mapping)
            or treasure["characteristics"].get("type_line") != "Token Artifact — Treasure"):
        return ()
    token = fixed_token_creation_node_capabilities(
        effects=(treasure,), target_schema=None, mechanic_ids={"cr-111-tokens"},
    )
    return (CAPABILITY, GRANTED_SELF_DEATH_RETURN_CAPABILITY, *token) if token else ()


def granted_death_treasure_runtime_dependencies(program):
    if program.provenance.get("template_id") != CHILD_TEMPLATE:
        return ()
    required = granted_death_treasure_effect_dependencies(program.effects)
    if (not required or (program.event, program.active_zone) != ("permanent.graveyard.self", "battlefield")
            or program.event_condition is not None or program.target_schema is not None or program.cost_schema is not None
            or program.handlers or program.provenance.get("granted_only") is not True):
        raise ValueError("The granted death trigger requires the tapped owner return and independent Treasure")
    return required


def validate_granted_death_treasure_child(program, lookup):
    if program.provenance.get("template_id") != PARENT_TEMPLATE:
        return
    if (len(program.effects) != 1 or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None):
        raise ValueError("The temporary death and Treasure ability must be granted by its exact instant or sorcery")
    effect = program.effects[0]
    target = TargetGroup.from_mapping(program.target_schema)
    if (effect.get("op") != "apply_source_characteristics_until_end_of_turn" or effect.get("schema_version") != 9
            or effect.get("card") != "$target.0" or target.zones != ("battlefield",)
            or target.categories != ("permanent",)
            or target.types_any != ("creature",) or target.min_targets != 1 or target.max_targets != 1):
        raise ValueError("The temporary characteristic grant requires its original target creature")
    fragments = canonical_ability_fragments(effect.get("ability_fragments", ()))
    if len(fragments) != 1 or not isinstance(fragments[0], GrantedTriggeredAbilitySpec):
        raise ValueError("The targeted creature must receive one independently bound death trigger")
    fragment = fragments[0]
    child = lookup(fragment.semantic_key)
    if (child is None or child.oracle_id != program.oracle_id or child.ability_id != fragment.ability_id
            or child.event != fragment.event or "granted_triggered_ability" not in child.coverage):
        raise ValueError("The temporary grant did not bind its original death trigger")
    required = granted_death_treasure_runtime_dependencies(child)
    if (not required or not set(required).issubset(child.capability_dependencies)
            or not set(required).issubset(program.capability_dependencies)):
        raise ValueError("The original death trigger must retain both independently authorized results")
