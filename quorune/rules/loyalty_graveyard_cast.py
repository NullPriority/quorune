"""Grant a paid cast only for the selected owned graveyard incarnation and turn."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from .cast_grant_riders import CastGrantRiderBinding
from .owner_library_position_shapes import has_exact_tap_price


LOYALTY_GRAVEYARD_CAST_CAPABILITY = "casting.permission.targeted_graveyard_loyalty"
LOYALTY_GRAVEYARD_CAST_MECHANIC = "targeted-graveyard-loyalty-cast-permission"
LOYALTY_GRAVEYARD_CAST_FIELD = "loyalty_graveyard_cast"
_CAST_MARKER = "loyalty_graveyard_cast_source"


def loyalty_graveyard_cast_effect():
    return {"op":"grant_cast_permission", "card":"$target.0", "player":"$controller",
            "zone":"graveyard", "types_any":["instant","sorcery"], LOYALTY_GRAVEYARD_CAST_FIELD:1}


def loyalty_graveyard_targets():
    from .graveyard_card_targets import targeted_graveyard_cast_schema

    return targeted_graveyard_cast_schema("you")


def has_closed_loyalty_minus_three(cost):
    if not isinstance(cost, Mapping) or cost.get("tap_source") is not False:
        return False
    text = cost.get("text")
    if type(text) is not str or text.replace("−","-") != "-3":
        return False
    return type(cost.get("loyalty_delta")) is int and cost["loyalty_delta"] == -3 and has_exact_tap_price(
        {**cost,"loyalty_delta":None,"tap_source":True,"text":"{T}"},
    )


def loyalty_graveyard_cast_dependencies(effects, target_schema, active_zone, cost_schema):
    if (thaw_value(effects) != [loyalty_graveyard_cast_effect()] or type(effects[0][LOYALTY_GRAVEYARD_CAST_FIELD]) is not int
            or thaw_value(target_schema) != loyalty_graveyard_targets()
            or active_zone != "battlefield" or not has_closed_loyalty_minus_three(cost_schema)):
        return ()
    return (LOYALTY_GRAVEYARD_CAST_CAPABILITY, "target.revalidate_resolution",
            "casting.permission.source_graveyard_exile", "zone.impulse_access.fixed")


def loyalty_graveyard_cast_runtime_dependencies(program):
    if (LOYALTY_GRAVEYARD_CAST_CAPABILITY not in program.capability_dependencies
            and not any(LOYALTY_GRAVEYARD_CAST_FIELD in effect for effect in program.effects)):
        return ()
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
    from ..compiler.activated_costs import activated_ability_cost

    required = loyalty_graveyard_cast_dependencies(
        program.effects, program.target_schema, program.active_zone, program.cost_schema,
    )
    if (not required or program.event != "activate" or program.event_condition is not None
            or program.provenance.get("granted_only") or len(program.handlers) != 1):
        raise ValueError("A graveyard cast permission needs its complete original loyalty activation.")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (ability.builtin_semantic_key != program.key or thaw_value(ability.target_schema) != loyalty_graveyard_targets()
            or not has_closed_loyalty_minus_three(activated_ability_cost(ability))):
        raise ValueError("The announced graveyard permission changed its loyalty price or target.")
    return required


def grant_loyalty_graveyard_cast_permission(host, effect, *, actor, reason):
    source = ResolutionEffectSource.from_effect(effect)
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (item is None or item.kind != "activated_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True or item.context.get("targets_revalidated") is not True
            or tuple(item.targets) != (effect.get("card"),) or source.object_id != item.source_object_id):
        raise GameRuleError("The graveyard cast permission requires its revalidated original target.")
    program = host.semantics.get(item.semantic_key)
    expected = {**loyalty_graveyard_cast_effect(),"card":effect["card"],"player":actor}
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not loyalty_graveyard_cast_runtime_dependencies(program)
            or type(effect.get(LOYALTY_GRAVEYARD_CAST_FIELD)) is not int
            or {key:value for key,value in thaw_value(effect).items() if key not in {"reason","_runtime_source"}} != expected):
        raise GameRuleError("The graveyard cast permission needs its exact original loyalty authority.")
    card = host._resolve_object(actor, effect["card"], zones={"graveyard"}, owned_only=True)
    if not card.is_card_object or card.face_down:
        raise GameRuleError("The graveyard cast permission requires its current physical card.")
    card.annotations[LOYALTY_GRAVEYARD_CAST_FIELD] = {
        "schema_version":1,"player":actor,"identity":card.logical_object_id,
        "turn_sequence":host.state.turn_sequence,"source":source.stack_ref,
    }
    host._log(actor, "permission.cast", "The selected card may be cast from its graveyard this turn.",
              {"object":card.ref,"player":actor,"reason":reason}, importance=2,
              changed_objects=[card.object_id],changed_players=[actor])
    return card.ref


def current_loyalty_graveyard_cast_permission(state, seat, card):
    raw = card.annotations.get(LOYALTY_GRAVEYARD_CAST_FIELD)
    if (card.zone != "graveyard" or card.owner != seat or card.face_down or not card.is_card_object
            or seat not in state.players or not state.players[seat].in_game
            or not isinstance(raw,Mapping) or set(raw) != {"schema_version","player","identity","turn_sequence","source"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["player"] != seat
            or raw["identity"] != card.logical_object_id or type(raw["turn_sequence"]) is not int
            or raw["turn_sequence"] != state.turn_sequence or type(raw["source"]) is not str or not raw["source"]):
        return None
    return {"player":seat,"zone":"graveyard","without_mana_cost":False,"allow_land":False,
            "allow_spell":True,"turn_sequence":state.turn_sequence,"source":raw["source"],
            _CAST_MARKER:raw["source"]}


def mark_loyalty_graveyard_cast_options(options, temporary_permission, *, excluded):
    if excluded or temporary_permission is None or _CAST_MARKER not in temporary_permission:
        return options
    return [
        {**option,_CAST_MARKER:temporary_permission[_CAST_MARKER]}
        if not any(term in str(option.get("id") or "") for term in ("flashback","escape","disturb","aftermath","jump-start","retrace"))
        else option for option in options
    ]


def commit_loyalty_graveyard_cast_use(selected, card, proposal):
    from .turn_cast_grant_model import TURN_CAST_GRANT_FIELD
    from ..zone_object_state import mark_cast_grant_rider

    marker = selected.get(_CAST_MARKER)
    if marker is None or TURN_CAST_GRANT_FIELD in selected:
        return
    if type(marker) is not str or not marker or proposal.origin != "graveyard" or card.zone != "stack" or card.owner != proposal.seat:
        raise GameRuleError("A graveyard cast lost its original loyalty permission.")
    mark_cast_grant_rider(card, CastGrantRiderBinding("graveyard_exile", card.logical_object_id, proposal.seat, marker))
