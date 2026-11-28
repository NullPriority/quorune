"""Exact intervening draw for an entering creature that dealt combat damage."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..event_counter_reference import event_object_identity
from ..public_history_query_model import (
    PUBLIC_HISTORY_QUERY_CAPABILITY, PublicHistoryQuerySpec, matching_public_history_events,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .optional_draw_discard import is_entry_exempt_draw_discard, validate_entry_exempt_draw_discard


COMBAT_DAMAGE_ENTRY_FIELD = "combat_damage_dealer_entered_this_turn"
COMBAT_DAMAGE_ENTRY_COVERAGE = "combat-damage-dealer-entry-this-turn"
COMBAT_DAMAGE_ENTRY_CONTEXT = "combat_damage_dealer_entry_identity"
COMBAT_DAMAGE_ENTRY_CAPABILITIES = (PUBLIC_HISTORY_QUERY_CAPABILITY, "trigger.condition.fixed_public_state")

_BASE_CONDITIONS = (
    {"field": "source_controller", "op": "eq", "value": "$source.controller"},
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
)
_ENTRY_CONDITION = {"field": COMBAT_DAMAGE_ENTRY_FIELD, "op": "eq", "value": True}
_ENTRY_QUERY = PublicHistoryQuerySpec(event="permanent_entered", relation="any_player")
_DRAW = {"op": "draw", "player": "$controller", "count": 1, "private": True}


def combat_damage_entry_condition(base):
    if not isinstance(base, Mapping) or thaw_value(FrozenMap(base)) != {"all": list(_BASE_CONDITIONS)}:
        return None
    return {"all": [*(dict(leaf) for leaf in _BASE_CONDITIONS), dict(_ENTRY_CONDITION)]}


def is_combat_damage_entry_condition(condition):
    if not isinstance(condition, Mapping) or set(condition) != {"all"}:
        return False
    leaves = condition["all"]
    if not isinstance(leaves, (list, tuple)) or len(leaves) != 4:
        return False
    exact = {"all": [*_BASE_CONDITIONS, _ENTRY_CONDITION]}
    return thaw_value(FrozenMap(condition)) == exact and leaves[2]["value"] is True and leaves[3]["value"] is True


def _uses_combat_damage_entry_condition(condition):
    if isinstance(condition, Mapping):
        return condition.get("field") == COMBAT_DAMAGE_ENTRY_FIELD or any(
            _uses_combat_damage_entry_condition(child) for child in condition.values()
        )
    if isinstance(condition, (list, tuple)):
        return any(_uses_combat_damage_entry_condition(child) for child in condition)
    return False


def combat_damage_entry_runtime_dependencies(program):
    if any(is_entry_exempt_draw_discard(effect) for effect in program.effects):
        return _entry_exempt_draw_discard_program_dependencies(program)
    marked = COMBAT_DAMAGE_ENTRY_COVERAGE in program.coverage
    if not marked and not _uses_combat_damage_entry_condition(program.event_condition):
        return ()
    if (not marked or "intervening_condition" not in program.coverage
            or program.event != "damage.dealt" or program.active_zone != "battlefield"
            or not is_combat_damage_entry_condition(program.event_condition)
            or program.target_schema is not None or len(program.effects) != 1
            or dict(program.effects[0]) != _DRAW or type(program.effects[0]["count"]) is not int
            or program.effects[0]["private"] is not True):
        raise ValueError("The combat dealer entry condition requires its exact public event and private draw")
    return COMBAT_DAMAGE_ENTRY_CAPABILITIES


def _damage_dealer_identity(context):
    types = context.get("source_types")
    amount = context.get("amount")
    if (context.get("combat") is not True or context.get("target_kind") != "player"
            or not isinstance(types, (list, tuple)) or "creature" not in types
            or type(amount) is not int or amount < 1):
        raise ValueError("The entry condition requires the creature that dealt positive combat damage to a player")
    identity = event_object_identity("damage.dealt", context)
    if identity["reference"] != context.get("source"):
        raise ValueError("The entry condition lost the normalized damage dealer")
    return identity


def _entered_this_turn(state, context, identity):
    dealer_controller = context.get("source_controller")
    if type(dealer_controller) is not str or dealer_controller not in state.players:
        raise ValueError("The combat dealer has no verified controller")
    events = matching_public_history_events(state, dealer_controller, _ENTRY_QUERY)
    if events is None or any(type(event.object_incarnation) is not str or not event.object_incarnation for event in events):
        raise ValueError("The combat dealer entry condition requires current committed entry identities")
    return any(event.object_incarnation == identity for event in events)


def combat_damage_dealer_entered_this_turn(state, context):
    return _entered_this_turn(state, context, _damage_dealer_identity(context)["logical_object_id"])


def combat_damage_entry_trigger_context(program, event, context):
    if COMBAT_DAMAGE_ENTRY_COVERAGE not in program.coverage:
        return {}
    if event != "damage.dealt":
        raise ValueError("The combat dealer entry witness must come from the actual damage occurrence")
    return {COMBAT_DAMAGE_ENTRY_CONTEXT: _damage_dealer_identity(context)}


def recheck_combat_damage_entry(state, context):
    identity = context.get(COMBAT_DAMAGE_ENTRY_CONTEXT)
    if (context.get("event") != "damage.dealt" or not isinstance(identity, Mapping)
            or set(identity) != {"reference", "logical_object_id"}
            or any(type(value) is not str or not value for value in identity.values())
            or identity["reference"] != context.get("source")
            or context.get("source_identity_key") != identity["logical_object_id"] + "|battlefield"):
        raise ValueError("The combat dealer entry condition lost its sealed damage incarnation")
    return _entered_this_turn(state, context, identity["logical_object_id"])


_SELF_COMBAT_DAMAGE = FrozenMap({"all": [
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
    {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
]})


def _entry_exempt_draw_discard_program_dependencies(program):
    from .fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities

    if ((program.event, program.active_zone) != ("damage.dealt.self", "battlefield")
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != _SELF_COMBAT_DAMAGE
            or program.event_condition["all"][1]["value"] is not True
            or program.target_schema is not None or program.cost_schema is not None
            or len(program.effects) != 1 or program.handlers):
        raise ValueError("Entry-exempt discard requires the original creature's combat damage to a player")
    validate_entry_exempt_draw_discard(program.effects[0])
    capabilities = fixed_optional_effect_node_capabilities(
        effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
    )
    if PUBLIC_HISTORY_QUERY_CAPABILITY not in capabilities:
        raise ValueError("Entry-exempt discard lacks its public history and private effect authorities")
    return (*capabilities, "trigger.event.normalized_damage", "trigger.placement.apnap")


def bind_entry_exempt_draw_discard(host, item, effect, *, resuming_actor=None):
    """Resolve the entry exception for the actual damage incarnation."""
    if not is_entry_exempt_draw_discard(effect):
        return effect
    try:
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or item.kind != "triggered_ability" or not item.context.get("currently_resolving")):
            raise ValueError("Entry-exempt discard requires its trusted resolving ability")
        _entry_exempt_draw_discard_program_dependencies(program)
        context = item.context
        identity = _damage_dealer_identity(context)["logical_object_id"]
        if (context.get("event") != "damage.dealt" or context.get("source_zone") != "battlefield"
                or context.get("source_identity_key") != identity + "|battlefield"
                or not item.source_object_id or context.get("source_object_id") != item.source_object_id
                or context.get("source_controller") != item.controller):
            raise ValueError("Entry-exempt discard lost its original damage incarnation or controller")
        expected = thaw_value(FrozenMap(host._semantic_value(program.effects[0], item)))
        supplied = thaw_value(FrozenMap(effect))
        if resuming_actor is None and supplied != expected:
            raise ValueError("Entry-exempt discard changed its original instructions")
        if _entered_this_turn(host.state, context, identity):
            expected["effects"] = expected["effects"][:1]
        if resuming_actor is not None and (resuming_actor != item.controller or supplied != expected):
            raise ValueError("Entry-exempt discard changed its recorded chooser or entry exception")
        return expected
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
