"""Reuse affected-player choice owners and the existing sealed-player contract."""
from __future__ import annotations

from collections.abc import Mapping

from ..compiler.event_player_choice_templates import (
    EVENT_PLAYER_CHOICE_CAPABILITY, EVENT_PLAYER_CHOICE_MECHANIC,
    SPELL_CASTER_LAND_RETURN_MECHANIC, spell_caster_land_return_effect,
)
from ..compiler.fixed_entry_return_requirements import FIXED_ENTRY_RETURN_MECHANIC
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
from .effect_shape_walkers import nested_effects
from .entry_return_capability_shapes import fixed_entry_return_node_capabilities
from .event_player_result_shapes import _event_player_reference_is_closed


_PLAYERS = frozenset({'$context.player', '$context.target', '$context.defending_player',
                      '$context.previous_controller', '$context.controller'})


def event_player_sacrifice_node_capabilities(*, effects, target_schema, mechanic_ids):
    if EVENT_PLAYER_CHOICE_MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping):
        return ()
    players = effect.get('players')
    if (not isinstance(players, (list, tuple)) or len(players) != 1
            or type(players[0]) is not str or players[0] not in _PLAYERS):
        return ()
    # The choice retains the ordinary spell/ability controller as its origin;
    # its existing player list independently selects the affected chooser.
    normalized = {**effect, 'players': ['$controller']}
    dependencies = fixed_affected_player_sacrifice_node_capabilities(
        effects=(normalized,), target_schema=None, mechanic_ids=mechanic_ids)
    return tuple(sorted({EVENT_PLAYER_CHOICE_CAPABILITY, *dependencies})) if dependencies else ()


def is_closed_event_player_sacrifice_program(program):
    required = event_player_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies) and all(
        _event_player_reference_is_closed(program, effect['players'][0]) for effect in program.effects)


def attacked_defender_exile_effect(*, actor="$controller", player="$context.defending_player"):
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": [player], "zone": "battlefield",
        "predicate": ObjectQuerySpec(zones=("battlefield",)).to_dict(), "count": 2,
        "then": "exile", "prompt": "Choose permanents to exile.",
    }


def attacked_defender_exile_effect_is_closed(effect, *, actor="$controller", player="$context.defending_player"):
    if (
        not isinstance(effect, Mapping) or type(effect.get("count")) is not int
        or effect != attacked_defender_exile_effect(actor=actor, player=player)
    ):
        return False
    try:
        predicate = ObjectQuerySpec.from_dict(effect["predicate"])
    except ObjectQueryError:
        return False
    return predicate == ObjectQuerySpec(zones=("battlefield",))


def is_typed_defender_exile(effect):
    return (
        effect.get("op") == "choose_cards_apnap" and effect.get("zone") == "battlefield"
        and effect.get("then") == "exile"
        and (effect.get("predicate") is not None
             or effect.get("players") == ["$context.defending_player"])
    )


def attacked_defender_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (
        target_schema is not None or len(effects) != 1 or not attacked_defender_exile_effect_is_closed(effects[0])
        or "trigger-event-normalized-self-attack" not in mechanic_ids
    ):
        return ()
    return (
        "trigger.event.normalized_self_attack", "trigger.placement.apnap",
        "trigger.event.normalized_zone_change", "zone.change.destination_replacement",
        "variant.commander.zone_return",
    )


def attacked_defender_exile_program_dependencies(program):
    if (
        (program.event, program.active_zone) != ("creature.attacks", "battlefield")
        or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
        or getattr(program, "cost_schema", None) is not None or getattr(program, "handlers", ())
    ):
        raise ValueError("Defender exile requires one sealed source attack")
    owned = attacked_defender_exile_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Defender exile requires its complete untargeted controlled-permanent choice")
    return owned

def _uses_spell_caster_land_return(effect):
    players = effect.get("players")
    return (
        effect.get("op") == "choose_cards_apnap" and effect.get("then") == "return_owner_hand"
        and isinstance(players, (list, tuple)) and "$context.player" in players
    )


def spell_caster_land_return_effect_is_closed(effect):
    if not _uses_spell_caster_land_return(effect) or type(effect.get("count")) is not int:
        return False
    if thaw_value(effect) != spell_caster_land_return_effect():
        return False
    try:
        return ObjectQuerySpec.from_dict(effect["predicate"]) == ObjectQuerySpec(zones=("battlefield",), types_all=("land",))
    except ObjectQueryError:
        return False


def _spell_caster_land_return_capabilities(effects, target_schema):
    if target_schema is not None or len(effects) != 1 or not spell_caster_land_return_effect_is_closed(effects[0]):
        return ()
    effect = {**thaw_value(effects[0]), "players": ["$controller"]}
    base = fixed_entry_return_node_capabilities(
        effects=(effect,), target_schema=None, mechanic_ids=(FIXED_ENTRY_RETURN_MECHANIC,),
    )
    if not base:
        return ()
    return (*base, "trigger.event.normalized_spell_cast", "trigger.placement.apnap",
            "zone.change.destination_replacement")


def spell_caster_land_return_runtime_dependencies(program):
    effects = (*program.effects, *getattr(program, "handlers", ()))
    if SPELL_CASTER_LAND_RETURN_MECHANIC not in program.coverage and not any(
        _uses_spell_caster_land_return(effect) for effect in nested_effects(effects)
    ):
        return ()
    required = _spell_caster_land_return_capabilities(program.effects, program.target_schema)
    if (
        not required or (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or program.event_condition is not None or getattr(program, "cost_schema", None) is not None
        or getattr(program, "handlers", ())
    ):
        raise ValueError("Caster land return requires the complete unqualified spell-cast trigger")
    return required


def event_bound_public_choice_recipient_has_left(host, raw_effect, effect):
    if not (attacked_defender_exile_effect_is_closed(raw_effect) or spell_caster_land_return_effect_is_closed(raw_effect)):
        return False
    players = effect.get("players")
    if not isinstance(players, (list, tuple)) or len(players) != 1:
        return False
    recipient = players[0]
    return (isinstance(recipient, str) and recipient in host.state.players
            and not host.state.players[recipient].in_game)

def event_player_choice_node_capabilities(*, effects, target_schema, mechanic_ids):
    """Resolve one of the independently closed affected-player choice results."""
    mechanics = tuple(mechanic_ids)
    if SPELL_CASTER_LAND_RETURN_MECHANIC in mechanics:
        return _spell_caster_land_return_capabilities(effects, target_schema)
    return (
        event_player_sacrifice_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
        or attacked_defender_exile_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    )
