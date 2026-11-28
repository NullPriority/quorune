"""A turn-bounded delayed token reward for controlled creatures damaging one opponent."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from ..target_source import capture_targeting_source
from .effect_shape_walkers import contains_nested_key
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities


COMBAT_TOKEN_FIELD = "controlled_opponent_combat_token"
COMBAT_TOKEN_CAPABILITY = "trigger.delayed.controlled_opponent_combat_token"
COMBAT_TOKEN_MECHANIC = "controlled-opponent-combat-token-this-turn"
COMBAT_TOKEN_LABEL = "Create a tapped Treasure for combat damage to the chosen opponent"
COMBAT_TOKEN_TARGET = {
    "zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1,
}


def combat_token_effect(token):
    return {"op": "delayed_trigger", COMBAT_TOKEN_FIELD: 1, "opponent": "$target.0", "token": token}


def _token_dependencies(token):
    if (
        not isinstance(token, Mapping) or token.get("name") != "Treasure Token"
        or type(token.get("quantity")) is not int or token["quantity"] != 1 or token.get("tapped") is not True
    ):
        return ()
    return fixed_token_creation_node_capabilities(
        effects=(thaw_value(token),), target_schema=None, mechanic_ids={"cr-111-tokens"},
    )


def controlled_combat_token_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (
        COMBAT_TOKEN_MECHANIC not in mechanic_ids or len(effects) != 1
        or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
        or FrozenMap(target_schema) != FrozenMap(COMBAT_TOKEN_TARGET)
    ):
        return ()
    effect, = effects
    token = effect.get("token")
    if type(effect.get(COMBAT_TOKEN_FIELD)) is not int or FrozenMap(effect) != FrozenMap(combat_token_effect(token)):
        return ()
    required = _token_dependencies(token)
    return (COMBAT_TOKEN_CAPABILITY, "trigger.event.normalized_damage", "trigger.placement.apnap",
            "target.revalidate_resolution", *required) if required else ()


def controlled_combat_token_program_dependencies(program):
    schema = program.target_schema
    if not (contains_nested_key((program.effects, program.handlers, schema), COMBAT_TOKEN_FIELD)
            or COMBAT_TOKEN_CAPABILITY in program.capability_dependencies):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or program.handlers
            or program.event_condition is not None):
        raise ValueError("A controlled combat token watch requires its original targeted spell")
    modes = schema.get("modes") if isinstance(schema, Mapping) else None
    scopes = [(program.effects, schema, program.coverage)]
    if isinstance(modes, Mapping):
        for mode in modes.values():
            if not isinstance(mode, Mapping) or not contains_nested_key(mode.get("effects"), COMBAT_TOKEN_FIELD):
                continue
            groups = mode.get("groups")
            if not isinstance(groups, (list, tuple)) or len(groups) != 1 or not isinstance(groups[0], Mapping):
                raise ValueError("The combat token mode requires one target group")
            target = {key: value for key, value in groups[0].items() if key != "id"}
            scopes.append((mode["effects"], target, mode.get("mechanics", ())))
    required = set()
    for effects, targets, mechanics in scopes:
        if not contains_nested_key(effects, COMBAT_TOKEN_FIELD):
            continue
        dependencies = controlled_combat_token_node_capabilities(
            effects=effects, target_schema=targets, mechanic_ids=mechanics,
        )
        if not dependencies:
            raise ValueError("A controlled combat token watch has an altered opponent target or token")
        required.update(dependencies)
    if not required:
        raise ValueError("A controlled combat token watch lacks its exact declared mode")
    return tuple(sorted(required))


def _active_instructions(item, program):
    from ..targets import mode_effects

    schema = program.target_schema
    if isinstance(schema, Mapping) and isinstance(schema.get("modes"), Mapping):
        groups = item.context.get("target_groups_current", item.context.get("target_groups"))
        return mode_effects(schema, item.modes, target_groups=groups)
    return program.effects


def bind_controlled_combat_token(host, item, raw):
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (
        item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True
        or program is None or not host.semantic_program_is_current_trusted(program)
        or not controlled_combat_token_program_dependencies(program)
    ):
        raise ValueError("The delayed combat token effect lost its resolving spell")
    if not any(FrozenMap(raw) == FrozenMap(effect) for effect in _active_instructions(item, program)):
        raise ValueError("The delayed combat token effect lost its chosen mode or target role")
    opponent = host._semantic_value(raw["opponent"], item)
    if opponent not in host.active_seats or opponent == item.controller:
        return None
    spell = host.state.cards.get(item.card_object_id or "")
    if spell is None or spell.zone != "stack" or spell.controller != item.controller:
        raise ValueError("The combat token watch requires the original casting source")
    return {
        "op": "delayed_trigger", "controller": item.controller, "label": COMBAT_TOKEN_LABEL,
        "event": "damage.dealt", "condition": {COMBAT_TOKEN_FIELD: 1, "combat": True,
            "source_controller": item.controller, "target_kind": "player", "target": opponent},
        "stack": {"label": COMBAT_TOKEN_LABEL, "context": {"dynamic_effects": [
            {**thaw_value(raw["token"]), "controller": item.controller},
        ]}},
        "once": False, "expires_turn_sequence": host.state.turn_sequence,
        "source_binding": capture_targeting_source(host, spell).to_dict(),
    }


def validate_bound_controlled_combat_token(host, effect, actor):
    binding = effect.get("source_binding")
    object_id = binding.get("object_id") if isinstance(binding, Mapping) else None
    item = next((item for item in host.state.stack if object_id and item.card_object_id == object_id), None)
    if item is None or item.controller != actor:
        raise ValueError("Only the originating spell can register its combat token watch")
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if program is None:
        raise ValueError("The delayed combat token effect lost its current program")
    instructions = _active_instructions(item, program)
    actual = FrozenMap({k: v for k, v in effect.items() if k not in {"reason", "_runtime_source"}})
    if not any(COMBAT_TOKEN_FIELD in raw and FrozenMap(bind_controlled_combat_token(host, item, raw) or {}) == actual
               for raw in instructions):
        raise ValueError("The delayed combat token watch changed its original target or duration")
