"""Typed blocked-creature declared-X grant and its own damage window."""

from collections.abc import Mapping

from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_from_dict
from ..object_predicate import ObjectQuerySpec
from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value


GRANT_VERSION = 34
WINDOW_VERSION = 48
OFFER_VERSION = 12
DESTINATION = "exile_then_resolving_cast"
OFFER_SCOPE = "own_combat_damage_top_window"
CAPABILITY = "resolution.effect.blocked_cast_x_damage_window"
GRANT_MECHANIC = "blocked-announced-x-temporary-damage-grant"
GRANT_OP = "apply_source_characteristics_until_end_of_turn"
WINDOW_OP = "fixed_library_selection"
GRANT_TEMPLATE = "blocked-creature-declared-x-damage-window-v1"
TRIGGER_TEMPLATE = "granted-player-combat-damage-own-top-window-v1"
DAMAGE_REFERENCE = "$context.amount"

_WINDOW_DEPS = (
    CAPABILITY, "library.select.fixed_controller", "zone.exile.fixed_top_resolving_cast",
    "zone.change.destination_replacement", "trigger.event.normalized_damage",
    "trigger.effect.fixed_event", "trigger.placement.apnap",
)
GRANT_DEPS = (
    *_WINDOW_DEPS, "continuous.ability.temporary_quoted_grant", "combat.damage.assignment.trample",
    DECLARED_AMOUNT_CAPABILITY, "target.revalidate_resolution",
)


def target_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
            "controller_relation": "you", "combat_state": "blocked_attacking", "count": 1}


def grant_effect(fragment):
    return {"op": GRANT_OP, "schema_version": GRANT_VERSION, "card": "$target.0",
            "power": CastXAmountSpec().to_dict(), "toughness": 0, "keywords": ["Trample"],
            "ability_fragments": [dict(fragment)]}


def window_predicate():
    return ObjectQuerySpec(zones=("library",), excluded_types=("land",))


def damage_window(actor="$controller", amount=DAMAGE_REFERENCE):
    return {"op": WINDOW_OP, "schema_version": WINDOW_VERSION, "player": actor, "look_count": amount,
            "public_reveal": False, "selected_reveal": True, "selection_policy": "up_to_matching",
            "minimum": 0, "maximum": 1, "predicate_groups": [[window_predicate().to_dict()]],
            "selected_destination": DESTINATION, "remainder_destination": "library_bottom",
            "remainder_order": "random"}


def damage_condition():
    from ..compiler.fixed_public_event_trigger_bindings import source_damage_incarnation_condition

    return {"all": [
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        source_damage_incarnation_condition(),
    ]}


def represents_grant(effect):
    return isinstance(effect, Mapping) and effect.get("op") == GRANT_OP and effect.get("schema_version") == GRANT_VERSION


def represents_window(effect):
    return isinstance(effect, Mapping) and effect.get("op") == WINDOW_OP and effect.get("schema_version") == WINDOW_VERSION


def grant_dependencies(effects, schema, *, resolved=False):
    if len(effects) != 1 or not represents_grant(effects[0]) or FrozenMap(schema or {}) != FrozenMap(target_schema()):
        return ()
    grant = effects[0]
    try:
        fragments = grant["ability_fragments"]
        if len(fragments) != 1:
            return ()
        source = ability_fragment_from_dict(fragments[0])
        if not isinstance(source, GrantedTriggeredAbilitySpec) or source.schema_version != 1 or source.event != "damage.dealt.self":
            return ()
        expected = grant_effect(fragments[0])
        if resolved:
            if type(grant.get("power")) is not int or grant["power"] < 0 or type(grant.get("card")) is not str:
                return ()
            expected.update(power=grant["power"], card=grant["card"])
        if (type(schema["count"]) is not int or type(grant.get("schema_version")) is not int
                or type(grant.get("toughness")) is not int or FrozenMap(grant) != FrozenMap(expected)):
            return ()
        if not resolved and CastXAmountSpec.from_dict(grant["power"]) != CastXAmountSpec():
            return ()
    except (TypeError, ValueError, KeyError):
        return ()
    return GRANT_DEPS


def window_dependencies(effect, *, actor=None):
    if not represents_window(effect):
        return ()
    amount = effect.get("look_count")
    if actor is not None and (type(amount) is not int or amount <= 0):
        raise ValueError("Private damage window requires a positive committed amount")
    expected = damage_window(actor, amount) if actor is not None else damage_window()
    if (any(type(effect.get(key)) is not int for key in ("schema_version", "minimum", "maximum"))
            or effect.get("public_reveal") is not False or effect.get("selected_reveal") is not True
            or FrozenMap(effect) != FrozenMap(expected)):
        raise ValueError("Private damage window requires its exact nonland exile and random bottom")
    return _WINDOW_DEPS


def program_dependencies(program):
    if any(represents_grant(effect) for effect in program.effects):
        required = grant_dependencies(program.effects, program.target_schema)
        if (not required or (program.event, program.active_zone) != ("resolve", "stack")
                or program.event_condition is not None or program.handlers):
            raise ValueError("Blocked declared-X grant requires its exact spell and creature target")
        return required
    if not any(represents_window(effect) for effect in program.effects):
        return ()
    if (len(program.effects) != 1 or (program.event, program.active_zone) != ("damage.dealt.self", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or not program.provenance.get("granted_only")
            or FrozenMap(program.event_condition or {}) != FrozenMap(damage_condition())):
        raise ValueError("Private damage window requires its independent granted player damage trigger")
    return window_dependencies(program.effects[0])


def resolving_window(host, *, stack_ref, actor):
    item = next((item for item in host.state.stack if item.ref == stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.controller != actor or item.kind != "triggered_ability"
            or item.context.get("currently_resolving") is not True
            or item.context.get("event") != "damage.dealt" or item.context.get("combat") is not True
            or item.context.get("target_kind") != "player" or program is None
            or not host.semantic_program_is_current_trusted(program)
            or CAPABILITY not in program_dependencies(program)):
        raise ValueError("The private damage window lost its authorized resolving damage trigger")
    amount = item.context.get("amount")
    if type(amount) is not int or amount <= 0:
        raise ValueError("The private damage window needs positive committed damage")
    return amount


def fixed_exile_offer_instruction(actor):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OPERATION

    return {"op": FIXED_TOP_EXILE_OPERATION, "schema_version": OFFER_VERSION,
            "player": actor, "scope": OFFER_SCOPE, "count": 1}


def validate_exile_offer_instruction(effect, *, resolved):
    if type(effect.get("schema_version")) is not int or type(effect.get("count")) is not int:
        raise ValueError("Damage-window cast offer requires its closed singleton scope")
    actor = effect.get("player")
    if (resolved and (type(actor) is not str or not actor or actor.startswith("$"))
            or not resolved and actor != "$controller"
            or FrozenMap(effect) != FrozenMap(fixed_exile_offer_instruction(actor))):
        raise ValueError("Damage-window cast offer requires its original resolving controller")
    return OFFER_SCOPE


def exile_offer(looked, selected, *, actor):
    from ..discover_model import discover_zone_counter
    from ..fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION
    from .library_selection import LibrarySelectionObjectIdentity

    if not selected:
        return ()
    identity, = [entry for entry in looked if entry.ref == selected[0]]
    next_incarnation = LibrarySelectionObjectIdentity(
        identity.object_id, f"{identity.object_id}@{discover_zone_counter(identity) + 1}", identity.ref,
    )
    return (FrozenMap({"op": FIXED_TOP_EXILE_OFFER_OPERATION,
                       "instruction": fixed_exile_offer_instruction(actor),
                       "results": [{"owner": actor, "identity": next_incarnation.to_dict()}]}),)


def validate_window_partition(host, *, actor, player, arrangement, cards, source_stack_ref,
                              looked_are_public, selected_are_public):
    amount = resolving_window(host, stack_ref=source_stack_ref, actor=actor)
    if (actor != player or looked_are_public or not selected_are_public
            or len(arrangement.looked) != min(amount, len(host.state.players[player].zones["library"]))):
        raise ValueError("The partition must retain the exact private damage window")
    if arrangement.selected_refs:
        card = cards[arrangement.selected_refs[0]]
        record = host.card_record(card)
        if record is None:
            raise ValueError("The window can select only an exact nonland card")
        front = record.faces[0].get("type_line", "") if record.faces else record.type_line
        if "land" in host._type_parts(front)[0]:
            raise ValueError("The damage window cannot exile a land")


def validate_grant_resolution(host, effect, instruction, *, actor):
    from ..continuous_effect_state import ResolutionEffectSource

    source = ResolutionEffectSource.from_effect(effect)
    item = next((entry for entry in host.state.stack if entry.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
            or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or type(item.x_value) is not int
            or len(item.targets) != 1 or item.targets[0] is None or program is None
            or not host.semantic_program_is_current_trusted(program) or not program_dependencies(program)):
        raise ValueError("Blocked X and trample require their currently resolving spell and original target")
    raw, = program.effects
    expected = {**raw, "power": item.x_value, "card": item.targets[0]}
    if (not grant_dependencies((instruction,), target_schema(), resolved=True)
            or FrozenMap(instruction) != FrozenMap(expected)):
        raise ValueError("Blocked X and trample changed their announced power, target or source grant")
    fragment = ability_fragment_from_dict(instruction["ability_fragments"][0])
    child = host.semantics.get(fragment.semantic_key)
    if (child is None or child.oracle_id != program.oracle_id or child.ability_id != fragment.ability_id
            or child.event != fragment.event or not host.semantic_program_is_current_trusted(child)
            or not program_dependencies(child)):
        raise ValueError("Blocked X and trample require the original independently admitted damage window")
