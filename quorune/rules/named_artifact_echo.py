"""Exact trigger scopes and result authority for named-artifact event echoes."""
from ..carddb_characteristics import public_object_rules_names
from ..replacement.immutable import FrozenMap, thaw_value
from ..source_entry_cast_information import source_entry_cast_condition
from ..token_copy_recipes import TokenCopyRecipeSpec

CAPABILITY = "trigger.effect.named_artifact_own_turn_echo"
CONDITION = "event_player_controls_named_artifact_on_own_turn"
RECIPIENT = "cast_entry_source_copy_recipient"
COVERAGE = "named_artifact_own_turn_echo"
OPPONENT = {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}
ENTRY_DEPS = (CAPABILITY, "trigger.effect.fixed_event", "trigger.placement.apnap",
              "trigger.event.normalized_zone_change", "trigger.condition.source_entry_cast",
              "token.creation.fixed_copy", "target.revalidate_resolution")
DRAW_DEPS = (CAPABILITY, "trigger.effect.fixed_event", "trigger.placement.apnap",
             "trigger.event.normalized_card_draw", "zone.draw.library_to_hand")
LIFE_DEPS = (CAPABILITY, "trigger.effect.fixed_event", "trigger.placement.apnap",
             "trigger.event.normalized_life_gain", "life.change.effect")


def entry_copy_effect():
    return {"op": "create_token", "controller": "$target.0", "quantity": 1,
            "copy_spec": TokenCopyRecipeSpec("source").to_dict(), RECIPIENT: 1}


def echo_effect(event):
    if event == "card.drawn":
        return {"op": "draw", "player": "$controller", "count": 1, "private": True}
    if event == "life.gained":
        return {"op": "life", "player": "$controller", "delta": "$context.amount"}
    raise ValueError("Name-qualified echo requires an actual draw or life gain")


def named_artifact_condition(printed_name):
    if type(printed_name) is not str or not printed_name.strip() or printed_name != printed_name.strip():
        raise ValueError("A named-artifact echo needs an exact printed name")
    return {"field": CONDITION, "op": "eq", "value": True, "name": printed_name}


def condition_holds(host, condition, source, context):
    name = condition.get("name")
    if thaw_value(condition) != named_artifact_condition(name):
        raise ValueError("Malformed named-artifact condition")
    player = context.get("player")
    if type(player) is not str or player == source.controller or player != host.state.active_player:
        return False
    if player not in host.active_seats:
        return False
    for object_id in host.state.players[player].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != player or card.phased_out:
            continue
        data = host._effective_card_data(card)
        if name in public_object_rules_names(card, data) and "artifact" in host._type_parts(str(data.get("type_line") or ""))[0]:
            return True
    return False


def program_dependencies(program):
    effects = program.effects
    condition = program.event_condition or {}
    entry = any(RECIPIENT in effect for effect in effects)
    if not (entry or condition.get("field") == CONDITION or CAPABILITY in program.capability_dependencies):
        return ()
    if program.active_zone != "battlefield" or program.cost_schema is not None or program.handlers:
        raise ValueError("The named-artifact echo requires its original battlefield ability")
    if entry:
        if (program.event != "permanent.enter.self" or FrozenMap(program.target_schema or {}) != FrozenMap(OPPONENT)
                or FrozenMap(condition) != FrozenMap(source_entry_cast_condition(any_caster=True))
                or tuple(FrozenMap(effect) for effect in effects) != (FrozenMap(entry_copy_effect()),)):
            raise ValueError("A cast-entry opponent copy changed its target, receipt or recipe")
        return ENTRY_DEPS
    if (program.event not in {"card.drawn", "life.gained"} or program.target_schema is not None
            or FrozenMap(condition) != FrozenMap(named_artifact_condition(condition.get("name")))
            or tuple(FrozenMap(effect) for effect in effects) != (FrozenMap(echo_effect(program.event)),)):
        raise ValueError("The named-artifact echo lost its exact observed event or result")
    return DRAW_DEPS if program.event == "card.drawn" else LIFE_DEPS


def resolved_entry_copy_recipient(host, item, effect):
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or program_dependencies(program) != ENTRY_DEPS or item.context.get("targets_revalidated") is not True
            or len(item.targets) != 1):
        raise ValueError("The token recipient lost its original legal cast-entry target")
    recipient, = item.targets
    expected = {**entry_copy_effect(), "controller": recipient}
    material = {k: thaw_value(v) for k, v in effect.items() if k not in {"_runtime_source","_replacement_selections","reason"}}
    if material != expected or type(material.get(RECIPIENT)) is not int or type(material.get("quantity")) is not int:
        raise ValueError("The cast-entry token recipient or recipe changed")
    return recipient if recipient in host.active_seats else None
