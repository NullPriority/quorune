"""One targeted attack roll and its printed copiable-value exception."""

from collections.abc import Mapping

from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_to_dict
from ..replacement.immutable import FrozenMap, thaw_value
from ..token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception
from .attacking_token_results import ATTACKING_TOKEN_OPERATION
from .random_selection import publish_resolution_die_roll


ROLL_OPERATION = "resolve_targeted_attack_die_copy"
ROLL_TEMPLATE = "targeted-attack-optional-d20-copy-v1"
ROLL_CAPABILITY = "random.die.targeted_attack_copy_repeat"
CLEANUP_TEMPLATE = "granted-end-combat-token-exile-v1"
CLEANUP_CAPABILITY = "trigger.step.granted_token_self_exile"
TOKEN_ENTRY_VERSION = 5
GRANTED_TEXT = "At end of combat, exile this token."
ATTACK_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
CLEANUP_CONDITION = {"all": [{"field": "step", "op": "eq", "value": "end_combat"}]}
TARGET_SCHEMA = {
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
    "controller_relation": "you", "count": 1,
}
CLEANUP_EFFECT = {
    "op": "move_if_in_zone", "card": "$source.zone_object", "from": "battlefield", "destination": "exile",
}


def roll_effect(grant):
    return {"op": ROLL_OPERATION, "player": "$controller", "copy_of": "$target.0", "grant": grant.to_dict()}


def decode_grant(raw):
    grant = GrantedTriggeredAbilitySpec.from_dict(raw)
    if grant.schema_version != 1 or grant.event != "step.begin" or grant.label != GRANTED_TEXT:
        raise ValueError("This token copy needs an intrinsic end-of-combat self-exile ability")
    return grant


def targeted_attack_die_copy_dependencies(program):
    template = program.provenance.get("template_id")
    if template not in {ROLL_TEMPLATE, CLEANUP_TEMPLATE} and not any(
        effect.get("op") == ROLL_OPERATION for effect in program.effects
    ):
        return ()
    if program.active_zone != "battlefield" or program.cost_schema is not None or program.handlers or len(program.effects) != 1:
        raise ValueError("The targeted attacking copy and its quoted ability need separate closed programs")
    if template == CLEANUP_TEMPLATE:
        if (program.event != "step.begin" or thaw_value(program.event_condition) != CLEANUP_CONDITION
                or program.target_schema is not None or not program.provenance.get("granted_only")
                or thaw_value(program.effects[0]) != CLEANUP_EFFECT):
            raise ValueError("The quoted end-of-combat self-exile ability must move its own incarnation")
        return (CLEANUP_CAPABILITY,)
    effect = thaw_value(program.effects[0])
    grant = decode_grant(effect.get("grant"))
    if (template != ROLL_TEMPLATE or program.event != "creature.attacks"
            or thaw_value(program.event_condition) != ATTACK_CONDITION
            or thaw_value(program.target_schema) != TARGET_SCHEMA or effect != roll_effect(grant)):
        raise ValueError("The targeted attacking copy requires its own declared attack and one controlled creature")
    return (ROLL_CAPABILITY, CLEANUP_CAPABILITY)


def _build_snapshot(host, target, grant):
    from ..token_creation import token_copy_snapshot

    snapshot = token_copy_snapshot(host, target)
    values = apply_copy_exception(snapshot["characteristics"], TokenCopyExceptionSpec(
        remove_legendary=True, add_ability_fragments=(ability_fragment_to_dict(grant),),
    ))
    return {**snapshot, "characteristics": values}


def validate_snapshot(value):
    if (not isinstance(value, Mapping)
            or set(value) != {"oracle_id", "printed_name", "annotations", "characteristics"}
            or not isinstance(value["characteristics"], Mapping)):
        raise ValueError("A repeated attacking copy requires its preserved copiable values")


def bind_targeted_attack_die_copy(host, item, raw, *, instruction_pointer):
    if item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True:
        raise ValueError("This die can be rolled only for the resolving targeted attack result")
    parent = host.semantics.get(item.semantic_key)
    if parent is None or not host.semantic_program_is_current_trusted(parent):
        raise ValueError("This die has no admitted resolving program")
    if targeted_attack_die_copy_dependencies(parent) != (ROLL_CAPABILITY, CLEANUP_CAPABILITY):
        raise ValueError("This die lost its closed targeted attack program")
    compiled = thaw_value(parent.effects[0])
    grant = decode_grant(compiled["grant"])
    child = host.semantics.get(grant.semantic_key)
    if (child is None or child.oracle_id != parent.oracle_id or child.ability_id != grant.ability_id
            or not host.semantic_program_is_current_trusted(child)
            or targeted_attack_die_copy_dependencies(child) != (CLEANUP_CAPABILITY,)):
        raise ValueError("The copy lost its admitted intrinsic end-of-combat trigger")
    target_ref, = item.targets
    if not isinstance(target_ref, str) or not target_ref:
        return None
    if thaw_value(FrozenMap(raw)) == compiled:
        target = host._resolve_object(item.controller, target_ref, zones={"battlefield"})
        snapshot = _build_snapshot(host, target, grant)
        original_pointer, ordinal = instruction_pointer, 1
    else:
        if (set(raw) != {"op", "player", "copy_of", "copy_snapshot", "stack_ref", "instruction_pointer", "ordinal"}
                or raw["stack_ref"] != item.ref or raw["player"] != item.controller or raw["copy_of"] != target_ref
                or type(raw["ordinal"]) is not int or raw["ordinal"] < 2):
            raise ValueError("The reroll lost its original targeted copy")
        snapshot, original_pointer, ordinal = raw["copy_snapshot"], raw["instruction_pointer"], raw["ordinal"]
    validate_snapshot(snapshot)
    face = publish_resolution_die_roll(
        host, controller=item.controller, stack_ref=item.ref, instruction_pointer=original_pointer,
        sides=20, roll_ordinal=ordinal,
    )
    return {
        "op": ROLL_OPERATION, "player": item.controller, "copy_of": target_ref, "copy_snapshot": snapshot,
        "stack_ref": item.ref, "instruction_pointer": original_pointer, "ordinal": ordinal, "face": face,
    }


def is_rolled_attacking_copy(effect):
    return effect.get("op") == ATTACKING_TOKEN_OPERATION and effect.get("token_entry_version") == TOKEN_ENTRY_VERSION


def is_repeat_offer(effect):
    children = effect.get("effects")
    return (effect.get("op") == "offer_optional_effect" and isinstance(children, (list, tuple))
            and len(children) == 1 and isinstance(children[0], Mapping) and children[0].get("op") == ROLL_OPERATION)


def validate_attacking_copy(effect, actor):
    if (
        set(effect) != {"op", "player", "quantity", "tapped", "copy_of", "copy_snapshot", "token_entry_version"}
        or not is_rolled_attacking_copy(effect) or type(effect["token_entry_version"]) is not int
        or effect["player"] != actor or type(effect["quantity"]) is not int or effect["quantity"] != 1
        or effect["tapped"] is not True or type(effect["copy_of"]) is not str or not effect["copy_of"]
    ):
        raise ValueError("A rolled attacking copy requires its original single tapped token result")
    validate_snapshot(effect["copy_snapshot"])


def rolled_copy_effects(effect):
    face = effect.get("face")
    roll = {key: value for key, value in effect.items() if key != "face"}
    if (set(roll) != {"op", "player", "copy_of", "copy_snapshot", "stack_ref", "instruction_pointer", "ordinal"}
            or type(face) is not int or not 1 <= face <= 20):
        raise ValueError("The optional repeat requires the public face of the committed roll")
    token = {
        "op": ATTACKING_TOKEN_OPERATION, "player": roll["player"], "quantity": 1, "tapped": True,
        "token_entry_version": TOKEN_ENTRY_VERSION, "copy_of": roll["copy_of"], "copy_snapshot": roll["copy_snapshot"],
    }
    validate_attacking_copy(token, roll["player"])
    if face < 15:
        return (FrozenMap(token),)
    optional = {"op": "offer_optional_effect", "player": roll["player"],
                "effects": [{**roll, "ordinal": roll["ordinal"] + 1}]}
    return (FrozenMap(token), FrozenMap(optional))
